"""Plan reconstruction adapters: apartment plan image(s) -> PlanDraft.

The real adapter asks an OpenAI-compatible multimodal LLM (Qwen3-vl) for a
single strict-JSON object describing walls in pixel coordinates, the
openings, and room names.  When the reply is not a valid ``PlanDraft`` it is
retried once with the validation error appended to the prompt.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from typing import Any, Protocol

from stroy.domain.plan import PlanDraft
from stroy.services.adapters import AdapterProtocolError, OpenAICompatibleLLM


class MultimodalLLMClient(Protocol):
    """Duck-typed subset of the OpenAI-compatible LLM client."""

    model: str
    profile_id: str | None

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]: ...


class PlanAnalysisAdapter(Protocol):
    """Analyze uploaded plan images into a structured PlanDraft."""

    def provenance(self) -> dict[str, Any]: ...

    async def analyze_plan(
        self,
        *,
        images: list[bytes],
        hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


_DECODER = json.JSONDecoder()

_PROMPT_TEMPLATE = """You are an architectural plan analyst. Read the attached apartment plan image and reply with ONE JSON object and nothing else.

Return exactly this shape:
{{
  "version": "0.1.0",
  "units": "mm",
  "scale": {{"source": "plan_label"|"manual"|"unknown", "mm_per_px": number|null}},
  "floors": [
    {{
      "name": "main",
      "level_mm": 0,
      "walls": [
        {{
          "id": "wall.1",
          "x1": number, "y1": number, "x2": number, "y2": number,
          "thickness_mm": number,
          "openings": [
            {{"id": "opening.1", "kind": "door"|"window"|"arch", "t": 0.5,
              "width_mm": number, "height_mm": number, "sill_mm": number|null}}
          ]
        }}
      ],
      "rooms": [
        {{"id": "room.1", "name": "Bedroom", "wall_ids": ["wall.1", "wall.2"], "floor_finish": null}}
      ]
    }}
  ]
}}

Rules:
- Give every wall a closed rectangle/loop of walls and list room wall_ids in order around the room.
- Coordinates and wall/opening sizes are PIXELS unless you found a dimension label on the plan.
- If the plan shows a dimension label with real millimetres, compute scale.mm_per_px (labelled mm divided by its pixel length) and set scale.source="plan_label".
- If no scale can be determined, set scale.source="unknown" and scale.mm_per_px=null.
- openings t is the position along the wall from 0 (start) to 1 (end); width_mm and height_mm are positive.
- wall length (from x1,y1 to x2,y2) must be at least thickness_mm.
- Do not invent furniture. Bare rooms only. No prose outside the JSON."""


def _data_url(image: bytes) -> str:
    image = _downscale(image)
    if image.startswith(b"\x89PNG"):
        media_type = "image/png"
    elif image.startswith(b"\xff\xd8"):
        media_type = "image/jpeg"
    else:
        raise AdapterProtocolError("plan adapter supports only PNG and JPEG images")
    encoded = base64.b64encode(image).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


PLAN_IMAGE_MAX_PIXELS = 2000
"""Longest side fed to the vision model.

Ollama vision encoding cost scales with image resolution; photos of plans are
routinely 4000+ px while floor-plan parsing does not benefit beyond ~2MP.
Downscaling here keeps adapter_timeout (300-900s) reachable on the home GPU
box (see #23 acceptance, 26.09 vision-encoding timings)."""

_DOWNSCALE_JPEG_QUALITY = 90


def _downscale(image: bytes) -> bytes:
    if len(image) <= 1_500_000:
        return image  # small assets go through untouched
    from io import BytesIO

    from PIL import Image, ImageOps

    try:
        with Image.open(BytesIO(image)) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail((PLAN_IMAGE_MAX_PIXELS, PLAN_IMAGE_MAX_PIXELS))
            buffer = BytesIO()
            img.save(buffer, format="JPEG", quality=_DOWNSCALE_JPEG_QUALITY)
            return buffer.getvalue()
    except Exception as exc:  # pragma: no cover - corrupt assets fail later anyway
        raise AdapterProtocolError(f"plan image could not be prepared: {exc}") from exc


def _build_messages(
    images: list[bytes],
    hints: dict[str, Any] | None,
    correction: str | None = None,
) -> list[dict[str, Any]]:
    prompt = _PROMPT_TEMPLATE
    hint_clause = _hint_clause(hints)
    if hint_clause:
        prompt = f"{prompt}\n\n{hint_clause}"
    if correction:
        prompt = f"{prompt}\n\nCorrection: {correction}"
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for image in images:
        content.append({"type": "image_url", "image_url": {"url": _data_url(image)}})
    return [
        {"role": "system", "content": "You reply with strict JSON only."},
        {"role": "user", "content": content},
    ]


def _hint_clause(hints: dict[str, Any] | None) -> str:
    if not hints:
        return ""
    known = hints.get("known_wall_length_mm")
    measured = hints.get("length_mm")
    if _positive(known) and _positive(measured):
        return (
            f"A wall known to be {known} mm is {measured} px long in this image: "
            f"use mm_per_px = {float(known) / float(measured)} and scale.source=\"manual\"."
        )
    return ""


def _positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _resolve_scale(
    raw_scale: Any,
    hints: dict[str, Any] | None,
) -> tuple[str, float | None]:
    source = "unknown"
    factor: float | None = None
    if isinstance(raw_scale, dict):
        raw_source = raw_scale.get("source")
        raw_factor = raw_scale.get("mm_per_px")
        if raw_source in ("plan_label", "manual") and _positive(raw_factor):
            source = str(raw_source)
            factor = float(raw_factor)
    if hints:
        known = hints.get("known_wall_length_mm")
        measured = hints.get("length_mm")
        if _positive(known) and _positive(measured):
            source = "manual"
            factor = float(known) / float(measured)
    return source, factor


def _scaled(value: Any, factor: float) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) * factor
    return value


def normalize_plan_draft(
    raw: dict[str, Any],
    hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert pixel geometry to millimetres when a scale is known.

    Returns a new dict (the caller's structure is not mutated).  When no scale
    is known the coordinates are kept as-read and ``scale.source`` is forced
    to ``"unknown"`` so a commit cannot silently create a scene without real
    dimensions.
    """
    draft = json.loads(json.dumps(raw))
    source, factor = _resolve_scale(draft.get("scale"), hints)
    if factor is None:
        draft["scale"] = {"source": "unknown", "mm_per_px": None}
        return draft

    draft["scale"] = {"source": source, "mm_per_px": factor}
    for floor in draft.get("floors") or []:
        if not isinstance(floor, dict):
            continue
        floor["level_mm"] = _scaled(floor.get("level_mm") or 0.0, factor)
        for wall in floor.get("walls") or []:
            if not isinstance(wall, dict):
                continue
            for key in ("x1", "y1", "x2", "y2", "thickness_mm"):
                wall[key] = _scaled(wall.get(key), factor)
            for opening in wall.get("openings") or []:
                if not isinstance(opening, dict):
                    continue
                for key in ("width_mm", "height_mm", "sill_mm"):
                    if opening.get(key) is not None:
                        opening[key] = _scaled(opening[key], factor)
    return draft


def _extract_draft(content: str) -> dict[str, Any]:
    # raw_decode instead of greedy loads: the model sometimes appends prose.
    start = content.find("{")
    if start == -1:
        raise ValueError("no JSON object found in the model reply")
    raw, _end = _DECODER.raw_decode(content[start:])
    if not isinstance(raw, dict):
        raise ValueError("plan reply must be a JSON object")
    return raw


class QwenPlanAdapter:
    """Ask a multimodal LLM for a PlanDraft and validate/normalize the reply."""

    def __init__(
        self,
        llm: MultimodalLLMClient,
        *,
        model_profile: str | None = None,
        attempts: int = 2,
    ) -> None:
        self.llm = llm
        self.model_profile = model_profile or llm.profile_id or llm.model
        self.attempts = max(1, attempts)

    def provenance(self) -> dict[str, Any]:
        return {
            "adapter": "qwen-plan",
            "model_profile": self.model_profile,
            "model": self.llm.model,
        }

    async def _complete(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        return await self.llm.complete(messages)

    @staticmethod
    def _reply_content(response: dict[str, Any]) -> str:
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AdapterProtocolError("malformed LLM response") from exc
        if not isinstance(content, str):
            raise AdapterProtocolError("LLM reply is not a text message")
        return content

    async def analyze_plan(
        self,
        *,
        images: list[bytes],
        hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not images:
            raise AdapterProtocolError("plan analysis requires at least one image")

        messages = _build_messages(images, hints)
        correction: str | None = None
        last_error: Exception | None = None
        for _attempt in range(self.attempts):
            if correction is not None:
                messages = _build_messages(images, hints, correction)
            try:
                reply = await self._complete(messages)
                raw = _extract_draft(self._reply_content(reply))
                normalized = normalize_plan_draft(raw, hints)
                draft = PlanDraft.model_validate(normalized)
                return {
                    "plan_draft": draft.model_dump(mode="json", exclude_none=True),
                    "adapter_provenance": self.provenance(),
                }
            except (ValueError, binascii.Error, AdapterProtocolError) as exc:
                last_error = exc
                correction = (
                    f"your previous answer was rejected ({exc}). "
                    "Return ONE valid JSON object only, no markdown fences."
                )
        raise AdapterProtocolError(
            f"plan model did not return a valid plan draft: {last_error}"
        )


def _mock_raw_draft() -> dict[str, Any]:
    walls = [
        {"id": "wall.1", "x1": 0, "y1": 0, "x2": 100, "y2": 0,
         "thickness_mm": 5,
         "openings": [{"id": "opening.1", "kind": "door", "t": 0.5,
                       "width_mm": 9, "height_mm": 21, "sill_mm": 0}]},
        {"id": "wall.2", "x1": 100, "y1": 0, "x2": 100, "y2": 80,
         "thickness_mm": 5, "openings": []},
        {"id": "wall.3", "x1": 100, "y1": 80, "x2": 0, "y2": 80,
         "thickness_mm": 5,
         "openings": [{"id": "opening.2", "kind": "window", "t": 0.5,
                       "width_mm": 40, "height_mm": 14, "sill_mm": 9}]},
        {"id": "wall.4", "x1": 0, "y1": 80, "x2": 0, "y2": 0,
         "thickness_mm": 5, "openings": []},
    ]
    return {
        "version": "0.1.0",
        "units": "mm",
        "scale": {"source": "plan_label", "mm_per_px": 10.0},
        "floors": [
            {
                "name": "main",
                "level_mm": 0.0,
                "walls": walls,
                "rooms": [
                    {
                        "id": "room.1",
                        "name": "Room 1",
                        "wall_ids": ["wall.1", "wall.2", "wall.3", "wall.4"],
                        "floor_finish": None,
                    }
                ],
            }
        ],
    }


class MockPlanAdapter:
    """Deterministic plan adapter for mocked/offline worker runs."""

    def provenance(self) -> dict[str, Any]:
        return {"adapter": "mock-plan", "model_profile": "mock-plan-v0"}

    async def analyze_plan(
        self,
        *,
        images: list[bytes],
        hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not images:
            raise AdapterProtocolError("plan analysis requires at least one image")
        normalized = normalize_plan_draft(_mock_raw_draft(), hints)
        draft = PlanDraft.model_validate(normalized)
        return {
            "plan_draft": draft.model_dump(mode="json", exclude_none=True),
            "adapter_provenance": self.provenance(),
        }


def build_local_plan_adapter() -> QwenPlanAdapter:
    """Build the real plan adapter from environment configuration."""
    model = os.getenv("STROY_LLM_MODEL")
    if not model:
        raise ValueError(
            "STROY_LLM_MODEL is required when the plan analyze adapter is local"
        )
    llm = OpenAICompatibleLLM(
        os.getenv("STROY_LLM_BASE_URL", "http://127.0.0.1:8001/v1"),
        os.getenv("STROY_LLM_API_KEY", "local"),
        model,
        profile_id=os.getenv("STROY_LLM_MODEL_PROFILE"),
        timeout_seconds=float(os.getenv("STROY_LLM_TIMEOUT_SECONDS", "120")),
    )
    return QwenPlanAdapter(llm)


__all__ = [
    "MockPlanAdapter",
    "PlanAnalysisAdapter",
    "QwenPlanAdapter",
    "build_local_plan_adapter",
    "normalize_plan_draft",
]
