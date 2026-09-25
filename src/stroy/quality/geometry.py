from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageFilter, ImageOps


def _edge_mask(data: bytes, *, threshold: int = 32) -> Image.Image:
    with Image.open(BytesIO(data)) as image:
        gray = ImageOps.grayscale(image.convert("RGB"))
        edges = gray.filter(ImageFilter.FIND_EDGES)
        mask = edges.point(lambda value: 255 if value >= threshold else 0, mode="1").convert("L")
        # FIND_EDGES marks the image border; clear it so framing does not dominate.
        pixels = mask.load()
        width, height = mask.size
        for x in range(width):
            pixels[x, 0] = 0
            pixels[x, height - 1] = 0
        for y in range(height):
            pixels[0, y] = 0
            pixels[width - 1, y] = 0
        return mask


def _count(mask: Image.Image) -> int:
    return sum(1 for value in mask.getdata() if value > 0)


def _overlap(source: Image.Image, target_dilated: Image.Image) -> int:
    return sum(
        1
        for source_value, target_value in zip(source.getdata(), target_dilated.getdata())
        if source_value > 0 and target_value > 0
    )


def geometry_edge_diagnostic(
    reference_rgb: bytes,
    generated_rgb: bytes,
    *,
    tolerance_px: int = 2,
) -> dict[str, Any]:
    reference = _edge_mask(reference_rgb)
    generated = _edge_mask(generated_rgb)
    if generated.size != reference.size:
        generated = generated.resize(reference.size, Image.Resampling.BILINEAR)
        generated = generated.point(lambda value: 255 if value >= 128 else 0, mode="1").convert("L")

    filter_size = max(3, tolerance_px * 2 + 1)
    reference_dilated = reference.filter(ImageFilter.MaxFilter(filter_size))
    generated_dilated = generated.filter(ImageFilter.MaxFilter(filter_size))

    reference_count = _count(reference)
    generated_count = _count(generated)
    matched_generated = _overlap(generated, reference_dilated)
    matched_reference = _overlap(reference, generated_dilated)

    precision = matched_generated / generated_count if generated_count else 0.0
    recall = matched_reference / reference_count if reference_count else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    return {
        "score": round(f1 * 100),
        "metrics": {
            "edge_f1": f1,
            "edge_precision": precision,
            "edge_recall": recall,
            "reference_edge_pixels": reference_count,
            "generated_edge_pixels": generated_count,
            "tolerance_px": tolerance_px,
        },
    }
