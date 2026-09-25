from __future__ import annotations

import base64
from dataclasses import dataclass
from io import BytesIO
import json
from typing import Any, Protocol

from PIL import Image, ImageStat

from stroy.services.adapters import AdapterProtocolError, OpenAICompatibleLLM
from stroy.style import StyleProfileProposal


@dataclass(frozen=True)
class StyleImage:
    asset_id: str
    media_type: str
    data: bytes


class StyleVisionAdapter(Protocol):
    def provenance(self) -> dict[str, Any]: ...

    async def analyze(
        self,
        source_text: str,
        images: list[StyleImage],
    ) -> StyleProfileProposal: ...


class LocalPixelStyleAdapter:
    """Deterministic image-aware fallback; no semantic vision model required."""

    def provenance(self) -> dict[str, Any]:
        return {"adapter": "local-pixel-style-v0", "model_profile": None}

    async def analyze(
        self,
        source_text: str,
        images: list[StyleImage],
    ) -> StyleProfileProposal:
        palette_counts: dict[str, int] = {}
        brightness_values: list[float] = []
        evidence_images: list[dict[str, Any]] = []

        for item in images:
            image = Image.open(BytesIO(item.data)).convert("RGB")
            thumb = image.copy()
            thumb.thumbnail((96, 96))
            quantized = thumb.quantize(colors=6).convert("RGB")
            colors = quantized.getcolors(maxcolors=96 * 96) or []
            colors.sort(reverse=True)
            for count, rgb in colors[:4]:
                hex_value = "#{:02X}{:02X}{:02X}".format(*rgb)
                palette_counts[hex_value] = palette_counts.get(hex_value, 0) + count
            brightness = float(ImageStat.Stat(thumb.convert("L")).mean[0])
            brightness_values.append(brightness)
            evidence_images.append(
                {
                    "asset_id": item.asset_id,
                    "width_px": image.width,
                    "height_px": image.height,
                    "mean_brightness": round(brightness, 2),
                }
            )

        palette = [
            {"hex": value, "role": "reference"}
            for value, _ in sorted(
                palette_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[:6]
        ]
        text = source_text.lower()
        labels = []
        if "миним" in text or "minimal" in text:
            labels.append("minimal")
        if "japandi" in text:
            labels.append("japandi")
        if "scandinav" in text or "сканди" in text:
            labels.append("scandinavian")
        if not labels:
            labels.append("reference-derived")

        average_brightness = (
            sum(brightness_values) / len(brightness_values)
            if brightness_values
            else 128.0
        )
        warm = any(token in text for token in ("warm", "тепл", "уют"))
        material_name = (
            "natural wood"
            if any(token in text for token in ("wood", "дерев"))
            else "reference-derived material"
        )
        return StyleProfileProposal.model_validate(
            {
                "labels": labels,
                "palette": palette,
                "materials": [
                    {
                        "name": material_name,
                        "finish": "matte",
                        "application": "reference-derived",
                    }
                ],
                "lighting": {
                    "temperature_k": 3000 if warm else 3500,
                    "intent": [
                        "soft" if average_brightness >= 100 else "moody",
                        "reference-derived",
                    ],
                },
                "forms": {"keywords": ["reference-derived proportions"]},
                "negative_constraints": [],
                "evidence": {
                    "adapter": "local-pixel-style-v0",
                    "images": evidence_images,
                },
            }
        )


class OpenAIVisionStyleAdapter:
    def __init__(self, llm: OpenAICompatibleLLM) -> None:
        self.llm = llm

    def provenance(self) -> dict[str, Any]:
        return {**self.llm.provenance(), "adapter": "openai-compatible-vision-style"}

    async def analyze(
        self,
        source_text: str,
        images: list[StyleImage],
    ) -> StyleProfileProposal:
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "Analyze the interior references and return ONLY one JSON object "
                    "with keys labels, palette, materials, lighting, forms, "
                    "negative_constraints, evidence. Palette entries use #RRGGBB. "
                    f"User intent: {source_text}"
                ),
            }
        ]
        for item in images:
            encoded = base64.b64encode(item.data).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{item.media_type};base64,{encoded}",
                    },
                }
            )

        raw = await self.llm.complete([{"role": "user", "content": content}])
        choices = raw.get("choices") or []
        if not choices:
            raise AdapterProtocolError("vision runtime returned no choices")
        message = choices[0].get("message") or {}
        payload = message.get("content")
        if not isinstance(payload, str):
            raise AdapterProtocolError("vision runtime content must be JSON text")
        cleaned = payload.strip()
        fence = chr(96) * 3
        if cleaned.startswith(fence):
            lines = cleaned.splitlines()
            if lines and lines[0].startswith(fence):
                lines = lines[1:]
            if lines and lines[-1].strip() == fence:
                lines = lines[:-1]
            cleaned = "\n".join(lines)
            if cleaned.lstrip().startswith("json"):
                cleaned = cleaned.lstrip()[4:].lstrip()
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise AdapterProtocolError("vision runtime returned invalid StyleProfile JSON") from exc
        return StyleProfileProposal.model_validate(parsed)
