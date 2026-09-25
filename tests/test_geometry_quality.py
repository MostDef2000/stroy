from io import BytesIO

from PIL import Image, ImageDraw

from stroy.quality import geometry_edge_score


def image_bytes(*, offset: int = 0) -> bytes:
    image = Image.new("RGB", (128, 96), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20 + offset, 20, 108 + offset, 76), outline="black", width=3)
    draw.line((20 + offset, 48, 108 + offset, 48), fill="black", width=2)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def protected_mask_bytes() -> bytes:
    image = Image.new("L", (128, 96), 0)
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, 118, 86), fill=255)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_aligned_geometry_scores_better_than_warped() -> None:
    reference = image_bytes(offset=0)
    aligned = geometry_edge_score(
        reference,
        image_bytes(offset=0),
        protected_mask_bytes=protected_mask_bytes(),
        tolerance_px=2,
    )
    warped = geometry_edge_score(
        reference,
        image_bytes(offset=12),
        protected_mask_bytes=protected_mask_bytes(),
        tolerance_px=2,
    )

    assert aligned["score"] > 0.95
    assert warped["score"] < aligned["score"] - 0.25
    assert aligned["edge_precision"] > warped["edge_precision"]
    assert aligned["edge_recall"] > warped["edge_recall"]


def test_mask_limits_diagnostic_to_protected_region() -> None:
    reference = image_bytes(offset=0)
    mask = Image.new("L", (128, 96), 0)
    draw = ImageDraw.Draw(mask)
    draw.rectangle((10, 10, 60, 86), fill=255)
    buffer = BytesIO()
    mask.save(buffer, format="PNG")

    score = geometry_edge_score(
        reference,
        image_bytes(offset=6),
        protected_mask_bytes=buffer.getvalue(),
        tolerance_px=2,
    )
    assert 0 <= score["score"] <= 1
    assert score["reference_edge_pixels"] > 0
