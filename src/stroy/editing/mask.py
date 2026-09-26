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
