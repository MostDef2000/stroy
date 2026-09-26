"""Server-side replacement mask rasterization (feather baked into the PNG)."""

import io

import pytest
from PIL import Image

from stroy.editing.mask import render_replacement_mask
from stroy.editing.replacement import ReplacementRegion


def _region(bbox, feather=8):
    return ReplacementRegion(
        target_entity_id="entity1",
        camera_id="cam1",
        bbox_px=bbox,
        feather_px=feather,
    )


def test_render_replacement_mask_scaling_and_feather():
    region = _region((100, 100, 200, 200), feather=8)

    # Camera 1000x800 -> base 1024x1024: sx=1.024, sy=1.28
    mask_bytes = render_replacement_mask(region, 1000, 800, 1024, 1024)
    img = Image.open(io.BytesIO(mask_bytes))
    assert img.size == (1024, 1024)
    assert img.mode == "L"

    # box: (102,128)-(205,256); core (feathered inward) is pure white,
    # background pure black, and the ramp rises monotonically toward the core.
    assert img.getpixel((150, 190)) == 255  # core
    assert img.getpixel((0, 0)) == 0  # background
    edge_values = [img.getpixel((102 + d, 190)) for d in range(8)]
    assert edge_values == sorted(edge_values)
    assert edge_values[0] > 0
    assert edge_values[-1] == 255
    assert img.getpixel((101, 190)) == 0  # just outside the box

    # Camera 1000x800 -> base 1248x832: sx=1.248, sy=1.04
    mask_bytes = render_replacement_mask(region, 1000, 800, 1248, 832)
    img = Image.open(io.BytesIO(mask_bytes))
    assert img.size == (1248, 832)
    assert img.getpixel((180, 160)) == 255  # core of (125,104)-(250,208)
    assert img.getpixel((124, 104)) == 0


def test_render_replacement_mask_clamping():
    region = _region((-10, -10, 1100, 900), feather=8)
    mask_bytes = render_replacement_mask(region, 1000, 800, 1000, 800)
    img = Image.open(io.BytesIO(mask_bytes))
    # clamped to the full frame; core still pure white away from feathered edges
    assert img.getpixel((100, 100)) == 255
    assert img.getpixel((899, 700)) == 255
    # feather ramp present at the frame border
    assert img.getpixel((0, 100)) < 255
    assert img.getpixel((2, 100)) > img.getpixel((0, 100))


def test_render_replacement_mask_degenerate():
    region = _region((1100, 1100, 1200, 1200))
    with pytest.raises(ValueError, match="mask fully outside frame"):
        render_replacement_mask(region, 1000, 800, 1000, 800)


def test_render_replacement_mask_tiny_box_feather_clamped():
    # box smaller than the feather radius must not crash or invert
    region = _region((500, 400, 503, 403), feather=8)
    mask_bytes = render_replacement_mask(region, 1000, 800, 1000, 800)
    img = Image.open(io.BytesIO(mask_bytes))
    assert img.getpixel((501, 401)) > 0
    assert max(img.getdata()) <= 255


def test_render_replacement_mask_interior_fully_feathered():
    # Regression: box where feather == (tx1-tx0)//2 leaves an empty
    # interior; the innermost ramp ring must reach 255 and PIL must not be
    # asked to draw an inverted rectangle (it raises ValueError).
    region = _region((275, 391, 725, 616), feather=18)
    # camera 1000x800 -> base 64x48 (sx=0.064, sy=0.06): box (18,23)-(46,37),
    # h=14 -> feather clamped to 7 = h//2, interior empty; ramp peaks at the
    # center rows 29-30 and falls off symmetrically toward both edges
    mask_bytes = render_replacement_mask(region, 1000, 800, 64, 48)
    img = Image.open(io.BytesIO(mask_bytes))
    assert img.size == (64, 48)
    # the innermost ramp ring reaches full white at the box center
    assert img.getpixel((32, 29)) == 255
    assert img.getpixel((32, 30)) == 255
    # vertical profile through the box: rises 7 steps to the center plateau,
    # then falls symmetrically
    column = [img.getpixel((32, y)) for y in range(23, 37)]
    assert column == [36, 72, 109, 145, 182, 218, 255, 255, 218, 182, 145, 109, 72, 36]
    assert max(img.getdata()) == 255
    assert img.getpixel((10, 32)) == 0  # background stays black
