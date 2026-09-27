from __future__ import annotations

import io

from PIL import Image, ImageDraw

from stroy.editing.replacement import ReplacementRegion


def render_replacement_mask(
    region: ReplacementRegion,
    camera_width_px: int,
    camera_height_px: int,
    target_width: int,
    target_height: int,
) -> bytes:
    """Rasterize a replacement region into a grayscale mask PNG.

    White (255) = edit region, black (0) = protected background. The bbox is
    given in CAMERA pixel space and is rescaled independently per axis to the
    base image size. A linear feather ramp is rendered INSIDE the box edges
    (server-side, because the box fork's FeatherMask node takes per-side
    integer expansions instead of a single feather radius). The PNG is
    consumed by ComfyUI ``ImageToMask(channel="red")`` — an L-mode PNG loads
    with replicated channels, so the red channel equals the gray value.
    """
    x0, y0, x1, y1 = region.bbox_px

    sx = target_width / camera_width_px
    sy = target_height / camera_height_px

    tx0 = max(0, min(round(x0 * sx), target_width))
    ty0 = max(0, min(round(y0 * sy), target_height))
    tx1 = max(0, min(round(x1 * sx), target_width))
    ty1 = max(0, min(round(y1 * sy), target_height))

    if tx1 <= tx0 or ty1 <= ty0:
        raise ValueError("mask fully outside frame")

    feather = max(0, int(region.feather_px))
    feather = min(feather, (tx1 - tx0) // 2, (ty1 - ty0) // 2)

    img = Image.new("L", (target_width, target_height), 0)
    draw = ImageDraw.Draw(img)

    # Linear ramp from the box edge (dark) toward the interior (bright):
    # band d covers the ring at distance d from the edge, drawn from the
    # outermost (darkest) to the innermost (brightest) ring. When the box
    # is at most twice the feather, the rings cover it entirely and the
    # innermost ring already reaches 255.
    for d in range(feather):
        value = int(255 * (d + 1) / feather)
        draw.rectangle([tx0 + d, ty0 + d, tx1 - 1 - d, ty1 - 1 - d], fill=value)
    if tx0 + feather <= tx1 - 1 - feather and ty0 + feather <= ty1 - 1 - feather:
        draw.rectangle(
            [tx0 + feather, ty0 + feather, tx1 - 1 - feather, ty1 - 1 - feather],
            fill=255,
        )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def crop_image_bytes(data: bytes, bbox: "list[int]") -> bytes:
    """Crop ``data`` (a decoded image) to ``bbox`` = [x, y, w, h] in pixels.

    Used to crop a full-frame reference asset down to its foreground object
    before it is handed to the edit executor as conditioning. A full-frame
    reference gives the model almost no object-level signal (the object is a
    tiny fraction of the frame), which collapses the inpaint to a mean-gray
    fill; cropping to the subject restores object transfer. The crop is
    clamped to image bounds; an empty/negative/out-of-bounds box is rejected.
    """
    if len(bbox) != 4:
        raise ValueError("reference_subject_bbox must have 4 elements [x, y, w, h]")
    x, y, w, h = bbox
    if w <= 0 or h <= 0:
        raise ValueError("reference_subject_bbox width/height must be positive")
    if x < 0 or y < 0:
        raise ValueError("reference_subject_bbox x/y must be non-negative")

    img = Image.open(io.BytesIO(data)).convert("RGB")
    iw, ih = img.size
    x1 = min(x + w, iw)
    y1 = min(y + h, ih)
    if x >= iw or y >= ih:
        raise ValueError("reference_subject_bbox is outside the image bounds")
    if x1 <= x or y1 <= y:
        raise ValueError("reference_subject_bbox produces an empty crop")

    cropped = img.crop((x, y, x1, y1))
    buf = io.BytesIO()
    cropped.save(buf, format="PNG")
    return buf.getvalue()
