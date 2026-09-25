from __future__ import annotations

from io import BytesIO
from typing import Literal

from PIL import Image, ImageChops, ImageFilter, ImageOps
from pydantic import BaseModel, ConfigDict, Field


class GeometryDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.1.0"] = "0.1.0"
    diagnostic_id: str = Field(min_length=1)
    scene_revision_id: str = Field(min_length=1)
    camera_id: str = Field(min_length=1)
    reference_asset_id: str = Field(min_length=1)
    generated_asset_id: str = Field(min_length=1)
    protected_mask_asset_id: str | None = None
    score: float = Field(ge=0, le=1)
    edge_precision: float = Field(ge=0, le=1)
    edge_recall: float = Field(ge=0, le=1)
    edge_f1: float = Field(ge=0, le=1)
    reference_edge_pixels: int = Field(ge=0)
    generated_edge_pixels: int = Field(ge=0)
    tolerance_px: int = Field(ge=0)
    advisory_threshold: float = Field(ge=0, le=1)
    advisory_pass: bool
    limitations: list[str] = Field(default_factory=list)


def _edge_mask(data: bytes, threshold: int) -> Image.Image:
    image = Image.open(BytesIO(data)).convert("L")
    edges = image.filter(ImageFilter.FIND_EDGES)
    return edges.point(lambda value: 255 if value >= threshold else 0, mode="1")


def _apply_mask(edge: Image.Image, mask_bytes: bytes | None) -> Image.Image:
    if mask_bytes is None:
        return edge
    mask = Image.open(BytesIO(mask_bytes)).convert("L").resize(edge.size)
    binary_mask = mask.point(lambda value: 255 if value >= 128 else 0, mode="1")
    return ImageChops.logical_and(edge, binary_mask)


def geometry_edge_score(
    reference_bytes: bytes,
    generated_bytes: bytes,
    *,
    protected_mask_bytes: bytes | None = None,
    edge_threshold: int = 24,
    tolerance_px: int = 2,
) -> dict[str, float | int]:
    reference = _apply_mask(_edge_mask(reference_bytes, edge_threshold), protected_mask_bytes)
    generated = _apply_mask(_edge_mask(generated_bytes, edge_threshold), protected_mask_bytes)
    if generated.size != reference.size:
        generated = generated.resize(reference.size)

    size = max(3, tolerance_px * 2 + 1)
    if size % 2 == 0:
        size += 1
    ref_dilated = reference.convert("L").filter(ImageFilter.MaxFilter(size)).point(
        lambda value: 255 if value else 0, mode="1"
    )
    gen_dilated = generated.convert("L").filter(ImageFilter.MaxFilter(size)).point(
        lambda value: 255 if value else 0, mode="1"
    )

    ref_count = sum(1 for value in reference.getdata() if value)
    gen_count = sum(1 for value in generated.getdata() if value)
    matched_generated = sum(
        1
        for edge, near_ref in zip(generated.getdata(), ref_dilated.getdata())
        if edge and near_ref
    )
    matched_reference = sum(
        1
        for edge, near_gen in zip(reference.getdata(), gen_dilated.getdata())
        if edge and near_gen
    )

    precision = matched_generated / gen_count if gen_count else 0.0
    recall = matched_reference / ref_count if ref_count else 0.0
    f1 = (
        2.0 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    return {
        "score": f1,
        "edge_precision": precision,
        "edge_recall": recall,
        "edge_f1": f1,
        "reference_edge_pixels": ref_count,
        "generated_edge_pixels": gen_count,
        "tolerance_px": tolerance_px,
    }
