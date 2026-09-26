from __future__ import annotations

import hashlib
from typing import Any, Protocol

from stroy.services.adapters import AdapterProtocolError
from stroy.style.models import (
    FormStyle,
    LightingStyle,
    MaterialStyle,
    PaletteEntry,
    StyleProfileProposal,
)


class VisionStyleAdapter(Protocol):
    """Protocol for adapters that analyze style from images."""

    def provenance(self) -> dict[str, Any]:
        ...

    async def analyze_style(
        self,
        *,
        images: list[bytes],
        source_text: str | None = None,
        input_asset_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        ...


class MockVisionStyleAdapter:
    """Deterministic mocked adapter for style vision analysis."""

    def provenance(self) -> dict[str, Any]:
        return {
            "adapter": "mock-vision",
            "model_profile": "mock-vision-v0",
        }

    async def analyze_style(
        self,
        *,
        images: list[bytes],
        source_text: str | None = None,
        input_asset_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        if not images:
            raise AdapterProtocolError("Vision analysis requires at least one image")

        # Deterministic mapping based on image bytes sha256
        # We use the first image to determine the "theme"
        first_img_hash = hashlib.sha256(images[0]).hexdigest()

        # Fixed reference outputs based on hash ranges
        # Hash range 0-3: "Modern/Minimal", 4-7: "Classic/Warm", 8-f: "Industrial/Cold"
        theme_key = first_img_hash[0]

        if theme_key in "0123":
            labels = ["minimal", "scandinavian"]
            palette = [
                PaletteEntry(hex="#F5F5F5", role="base"),
                PaletteEntry(hex="#A0A0A0", role="accent"),
            ]
            materials = [
                MaterialStyle(name="light ash wood", finish="matte", application="flooring"),
                MaterialStyle(name="white plaster", finish="smooth", application="walls"),
            ]
            lighting = LightingStyle(temperature_k=4000, intent=["bright", "diffused"])
            forms = FormStyle(keywords=["clean lines", "geometric", "uncluttered"])
        elif theme_key in "4567":
            labels = ["classic", "cozy"]
            palette = [
                PaletteEntry(hex="#EEDC82", role="base"),
                PaletteEntry(hex="#8B4513", role="accent"),
            ]
            materials = [
                MaterialStyle(name="dark walnut", finish="satin", application="furniture"),
                MaterialStyle(name="beige linen", finish="textured", application="upholstery"),
            ]
            lighting = LightingStyle(temperature_k=2700, intent=["warm", "intimate"])
            forms = FormStyle(keywords=["curved", "ornate", "weighted"])
        else:
            labels = ["industrial", "brutalist"]
            palette = [
                PaletteEntry(hex="#808080", role="base"),
                PaletteEntry(hex="#404040", role="accent"),
            ]
            materials = [
                MaterialStyle(name="exposed concrete", finish="raw", application="walls"),
                MaterialStyle(name="black steel", finish="powder-coated", application="frames"),
            ]
            lighting = LightingStyle(temperature_k=5000, intent=["cold", "direct"])
            forms = FormStyle(keywords=["raw", "exposed", "angular"])

        proposal = StyleProfileProposal(
            labels=labels,
            palette=palette,
            materials=materials,
            lighting=lighting,
            forms=forms,
            negative_constraints=[],
            evidence={
                "mode": "mock-vision",
                "reference_asset_mapping": {
                    f"img_{i}": hashlib.sha256(img).hexdigest()[:8]
                    for i, img in enumerate(images)
                },
            },
        )

        return {
            "style_profile": proposal.model_dump(mode="json"),
            "adapter_provenance": self.provenance(),
        }
