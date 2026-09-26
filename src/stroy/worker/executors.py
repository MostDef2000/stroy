from __future__ import annotations

import base64
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Protocol

from stroy.domain.models import Scene
from stroy.generation import GenerationContext, WorkflowManifest
from stroy.quality import GeometryDiagnostic, geometry_edge_score
from stroy.rendering import BlenderAdapter, RenderContext, build_blender_plan
from stroy.services.adapters import ComfyUIAdapter
from stroy.style.vision import VisionStyleAdapter, MockVisionStyleAdapter
from stroy.style.qwen_vision import build_local_vision_adapter


class LLMAdapter(Protocol):
    def provenance(self) -> dict[str, Any]: ...

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]: ...


def normalize_llm_result(raw: dict[str, Any]) -> dict[str, Any]:
    if "tool_calls" in raw and "choices" not in raw:
        return raw

    choices = raw.get("choices") or []
    if not choices:
        return {"content": None, "tool_calls": []}

    message = choices[0].get("message") or {}
    normalized_calls: list[dict[str, Any]] = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        name = function.get("name")
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON tool arguments for {name}") from exc
        if not isinstance(arguments, dict):
            raise ValueError(f"tool arguments for {name} must be an object")
        normalized_calls.append({"name": name, "arguments": arguments})

    return {
        "content": message.get("content"),
        "tool_calls": normalized_calls,
    }


class QwenExecutor:
    def __init__(self, adapter: LLMAdapter) -> None:
        self.adapter = adapter

    async def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload", {})
        raw = await self.adapter.complete(
            payload.get("messages", []),
            tools=payload.get("tools"),
        )
        result = normalize_llm_result(raw)
        result["adapter_provenance"] = self.adapter.provenance()
        return result


class ComfyUIExecutor:
    def __init__(
        self,
        adapter: ComfyUIAdapter,
        worker_id: str,
        model_profile_id: str,
    ) -> None:
        self.adapter = adapter
        self.worker_id = worker_id
        self.model_profile_id = model_profile_id
        self.active_prompts: dict[str, str] = {}

    async def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload", {})
        raw_manifest = payload.get("workflow_manifest")
        if not isinstance(raw_manifest, dict):
            raise ValueError("image job requires payload.workflow_manifest")
        manifest = WorkflowManifest.model_validate(raw_manifest)
        if manifest.model_profile != self.model_profile_id:
            raise ValueError(
                "workflow model profile does not match worker image profile: "
                f"{manifest.model_profile} != {self.model_profile_id}"
            )

        semantic_inputs = payload.get("inputs") or {}
        if not isinstance(semantic_inputs, dict):
            raise ValueError("image job payload.inputs must be an object")
        graph = manifest.materialize(semantic_inputs)

        prompt_id = await self.adapter.submit(graph, self.worker_id)
        job_id = job.get("job_id")
        if isinstance(job_id, str):
            self.active_prompts[job_id] = prompt_id
        generation = GenerationContext.model_validate(payload.get("generation") or {})
        try:
            history = await self.adapter.wait(
                prompt_id,
                timeout_seconds=int(payload.get("timeout_seconds", 900)),
            )
            artifacts = await self.adapter.collect_output_images(history)
        finally:
            if isinstance(job_id, str):
                self.active_prompts.pop(job_id, None)
        return {
            "prompt_id": prompt_id,
            "history": history,
            "workflow": {
                "id": manifest.id,
                "version": manifest.version,
            },
            "model_profile": manifest.model_profile,
            "semantic_outputs": manifest.outputs,
            "adapter_provenance": self.adapter.provenance(),
            "_artifacts": artifacts,
            "_generation_context": generation.model_dump(mode="json"),
        }


    async def cancel(self, job: dict[str, Any]) -> None:
        job_id = job.get("job_id")
        if not isinstance(job_id, str):
            return
        prompt_id = self.active_prompts.get(job_id)
        if prompt_id:
            await self.adapter.cancel(prompt_id)



class VisionStyleExecutor:
    def __init__(self, adapter: VisionStyleAdapter) -> None:
        self.adapter = adapter

    async def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload", {})
        images = payload.get("images")

        if isinstance(images, list) and images and all(isinstance(img, bytes) for img in images):
            image_bytes = images
        else:
            asset_ids = payload.get("input_asset_ids") or []
            if not asset_ids:
                job_id = job.get("job_id", "default")
                image_bytes = [hashlib.sha256(str(job_id).encode()).digest()]
            else:
                image_bytes = [
                    hashlib.sha256(aid.encode()).digest() for aid in asset_ids
                ]

        result = await self.adapter.analyze_style(
            images=image_bytes,
            source_text=payload.get("source_text"),
            input_asset_ids=payload.get("input_asset_ids"),
        )
        return result


class FakeStyleExecutor:
    async def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload", {})
        source_text = str(payload.get("source_text", "")).lower()

        warm = any(token in source_text for token in ("warm", "тепл", "уют"))
        minimal = any(token in source_text for token in ("minimal", "миним"))
        wood = any(token in source_text for token in ("wood", "дерев"))

        labels = ["minimal"] if minimal else ["contemporary"]
        palette = [
            {"hex": "#D8D0C4", "role": "base"},
            {"hex": "#8A8178", "role": "accent"},
        ]
        materials = [
            {
                "name": "natural wood" if wood else "matte plaster",
                "finish": "matte",
                "application": "primary surfaces",
            }
        ]
        lighting = {
            "temperature_k": 3000 if warm else 3500,
            "intent": ["soft", "ambient"],
        }
        forms = {
            "keywords": ["clean lines", "rounded accents"] if minimal else ["balanced proportions"]
        }
        return {
            "style_profile": {
                "labels": labels,
                "palette": palette,
                "materials": materials,
                "lighting": lighting,
                "forms": forms,
                "negative_constraints": [],
                "evidence": {
                    "mode": "fake-style",
                    "reference_asset_ids": list(payload.get("input_asset_ids") or []),
                },
            },
            "adapter_provenance": {
                "adapter": "fake-style",
                "model_profile": "fake",
            },
        }



class BlenderExecutor:
    def __init__(self, adapter: BlenderAdapter) -> None:
        self.adapter = adapter

    async def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload", {})
        raw_scene = payload.get("scene")
        if not isinstance(raw_scene, dict):
            raise ValueError("render job requires payload.scene")
        scene = Scene.model_validate(raw_scene)

        scene_revision_id = payload.get("scene_revision_id")
        camera_id = payload.get("camera_id")
        render_id = payload.get("render_id")
        if not all(
            isinstance(value, str) and value
            for value in (scene_revision_id, camera_id, render_id)
        ):
            raise ValueError(
                "render job requires render_id, scene_revision_id and camera_id"
            )

        plan = build_blender_plan(
            scene,
            scene_revision_id=scene_revision_id,
            design_revision_id=payload.get("design_revision_id"),
            camera_id=camera_id,
            renderer_profile=str(
                payload.get("renderer_profile", "blender-eevee-v0")
            ),
        )

        with tempfile.TemporaryDirectory(prefix="stroy-blender-") as tmp:
            outputs = await self.adapter.run(plan, output_dir=tmp, render=True)
            artifacts = []
            media_types = {
                "rgb": "image/png",
                "depth": "image/x-exr",
                "normals": "image/x-exr",
                "object_ids": "image/x-exr",
                "material_ids": "image/x-exr",
                "metadata": "application/json",
            }
            for semantic_name, path in outputs.items():
                artifacts.append(
                    {
                        "semantic_name": semantic_name,
                        "filename": Path(path).name,
                        "media_type": media_types[semantic_name],
                        "data": Path(path).read_bytes(),
                    }
                )

        return {
            "renderer_profile": plan.renderer_profile,
            "scene_metadata": json.loads(
                next(
                    item["data"]
                    for item in artifacts
                    if item["semantic_name"] == "metadata"
                ).decode("utf-8")
            ),
            "_artifacts": artifacts,
            "_render_context": RenderContext(
                render_id=render_id,
                scene_revision_id=scene_revision_id,
                design_revision_id=payload.get("design_revision_id"),
                camera_id=camera_id,
                renderer_profile=plan.renderer_profile,
            ).model_dump(mode="json", exclude_none=True),
        }



class GeometryQualityExecutor:
    def __init__(self, client) -> None:
        self.client = client

    async def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload", {})
        downloads = job.get("download_urls") or {}

        reference_asset_id = payload.get("reference_asset_id")
        generated_asset_id = payload.get("generated_asset_id")
        protected_mask_asset_id = payload.get("protected_mask_asset_id")
        if not isinstance(reference_asset_id, str) or not isinstance(generated_asset_id, str):
            raise ValueError("quality job requires reference_asset_id and generated_asset_id")

        try:
            reference_url = downloads[reference_asset_id]
            generated_url = downloads[generated_asset_id]
        except KeyError as exc:
            raise ValueError("quality job input download URL is missing") from exc

        reference_bytes = await self.client.download_input(reference_url)
        generated_bytes = await self.client.download_input(generated_url)
        protected_mask_bytes = None
        if isinstance(protected_mask_asset_id, str):
            mask_url = downloads.get(protected_mask_asset_id)
            if not mask_url:
                raise ValueError("quality job protected mask download URL is missing")
            protected_mask_bytes = await self.client.download_input(mask_url)

        metrics = geometry_edge_score(
            reference_bytes,
            generated_bytes,
            protected_mask_bytes=protected_mask_bytes,
            edge_threshold=int(payload.get("edge_threshold", 24)),
            tolerance_px=int(payload.get("tolerance_px", 2)),
        )
        threshold = float(payload.get("advisory_threshold", 0.72))
        diagnostic = GeometryDiagnostic(
            diagnostic_id=str(payload["diagnostic_id"]),
            scene_revision_id=str(payload["scene_revision_id"]),
            camera_id=str(payload["camera_id"]),
            reference_asset_id=reference_asset_id,
            generated_asset_id=generated_asset_id,
            protected_mask_asset_id=protected_mask_asset_id,
            advisory_threshold=threshold,
            advisory_pass=float(metrics["score"]) >= threshold,
            limitations=[
                "edge alignment is appearance-sensitive",
                "the baseline metric does not estimate metric depth",
                "threshold is advisory and never mutates canonical geometry",
            ],
            **metrics,
        )
        return {
            "geometry_diagnostic": diagnostic.model_dump(mode="json"),
            "adapter_provenance": {"adapter": "geometry-edge-v0"},
        }



def build_style_analyze_executor(adapter_name: str) -> Any:
    if adapter_name == "mock":
        return VisionStyleExecutor(MockVisionStyleAdapter())
    if adapter_name == "fake":
        return FakeStyleExecutor()
    if adapter_name == "local":
        return VisionStyleExecutor(build_local_vision_adapter())
    raise ValueError(f"unknown style vision adapter: {adapter_name}")

class FakeImageExecutor:
    """Deterministic image executor for full mocked edit/generation E2E."""

    _PNG = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )

    async def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload", {})
        generation = GenerationContext.model_validate(payload.get("generation") or {})
        workflow = WorkflowManifest.model_validate(payload.get("workflow_manifest") or {})
        return {
            "fake": True,
            "workflow": {"id": workflow.id, "version": workflow.version},
            "model_profile": workflow.model_profile,
            "adapter_provenance": {
                "adapter": "fake-image",
                "model_profile": workflow.model_profile,
            },
            "_artifacts": [
                {
                    "semantic_name": "image",
                    "filename": f"{generation.generation_id}.png",
                    "media_type": "image/png",
                    "data": self._PNG,
                }
            ],
            "_generation_context": generation.model_dump(mode="json"),
        }
