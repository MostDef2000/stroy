"""Server-side replacement mask rasterization (feather baked into the PNG)."""

import io

import pytest
from PIL import Image, ImageDraw

from stroy.editing import segmentation
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


def test_render_replacement_mask_client_override_identity_rescale():
    """A client_override region arrives in BASE-IMAGE pixel space; routes.py
    renders it with identity rescale (camera dims == target dims), so the
    box maps 1:1 onto the 1024x1024 base image."""
    region = ReplacementRegion(
        type="client_override",
        target_entity_id="entity1",
        camera_id="cam1",
        bbox_px=(334, 578, 654, 884),
        feather_px=24,
        source="client_mask_region",
    )
    mask_bytes = render_replacement_mask(region, 1024, 1024, 1024, 1024)
    img = Image.open(io.BytesIO(mask_bytes))
    assert img.size == (1024, 1024)
    assert img.mode == "L"
    # white interior covers exactly the box (inset by the 24px feather ramp)
    assert img.getpixel((494, 731)) == 255  # box center
    assert img.getpixel((100, 100)) == 0  # clearly outside the box
    # identity rescale: sx=sy=1.0, so box edges land exactly where supplied
    assert img.getpixel((333, 731)) == 0  # one px left of the box
    assert img.getpixel((358, 731)) == 255  # first core column (334 + feather)


# ---------------------------------------------------------------------------
# Silhouette shape (segmentation is stubbed; no rembg model/network needed)
# ---------------------------------------------------------------------------


def _stub_alpha(size=(256, 256)):
    alpha = Image.new("L", size, 0)
    ImageDraw.Draw(alpha).ellipse([120, 120, 180, 180], fill=255)
    return alpha


def test_render_replacement_mask_silhouette_uses_segmentation(monkeypatch):
    region = _region((100, 100, 200, 200), feather=8)
    stub = _stub_alpha()
    calls: dict = {}

    def fake_segment(image, bbox, model=None, *, feather=0):
        calls["bbox"] = bbox
        calls["feather"] = feather
        return stub

    monkeypatch.setattr(segmentation, "segment_subject", fake_segment)
    base = Image.new("RGB", (256, 256), "white")
    mask_bytes = render_replacement_mask(
        region, 256, 256, 256, 256, shape="silhouette", base_image=base
    )
    img = Image.open(io.BytesIO(mask_bytes))
    assert img.size == (256, 256)
    assert img.mode == "L"
    # the segmenter output is used verbatim (it already carries the feather)
    assert img.tobytes() == stub.tobytes()
    # zeros outside the bbox survive
    assert img.getpixel((0, 0)) == 0
    assert img.getpixel((250, 250)) == 0
    # the region bbox (target space) and clamped feather reach the segmenter
    assert calls["bbox"] == (100, 100, 200, 200)
    assert calls["feather"] == 8


def test_render_replacement_mask_silhouette_falls_back_to_rectangle(monkeypatch):
    region = _region((100, 100, 200, 200), feather=8)
    monkeypatch.setattr(segmentation, "segment_subject", lambda *a, **k: None)
    base = Image.new("RGB", (256, 256), "white")

    expected = render_replacement_mask(region, 256, 256, 256, 256)
    got = render_replacement_mask(
        region, 256, 256, 256, 256, shape="silhouette", base_image=base
    )
    assert got == expected


def test_render_replacement_mask_silhouette_without_image_falls_back():
    region = _region((100, 100, 200, 200), feather=8)
    expected = render_replacement_mask(region, 256, 256, 256, 256)
    got = render_replacement_mask(region, 256, 256, 256, 256, shape="silhouette")
    assert got == expected


def test_is_degenerate_alpha_guard():
    # <2% coverage: a single lit pixel in a 100x100 box
    empty = Image.new("L", (100, 100), 0)
    empty.putpixel((0, 0), 255)
    assert segmentation.is_degenerate_alpha(empty) is True

    # >98% coverage: almost the whole box is subject
    full = Image.new("L", (100, 100), 255)
    assert segmentation.is_degenerate_alpha(full) is True

    # a plausible silhouette (~25%) is accepted
    ok = Image.new("L", (100, 100), 0)
    ImageDraw.Draw(ok).rectangle([10, 10, 59, 59], fill=255)
    assert segmentation.is_degenerate_alpha(ok) is False


# ---------------------------------------------------------------------------
# segment_subject: real algorithm unit tests with a fake rembg module
# ---------------------------------------------------------------------------


class _FakeRemBg:
    """Stands in for the lazily-imported rembg module."""

    def __init__(self, cutout_fn):
        self.cutout_fn = cutout_fn
        self.crops: list = []

    def new_session(self, model):
        return {"model": model}

    def remove(self, crop, session=None):
        self.crops.append(crop.copy())
        return self.cutout_fn(crop)


def _opaque_center(crop):
    out = Image.new("RGBA", crop.size, (255, 0, 0, 0))
    w, h = crop.size
    ImageDraw.Draw(out).rectangle([w // 4, h // 4, w - 1 - w // 4, h - 1 - h // 4], fill=(255, 0, 0, 255))
    return out


def _patch_segmenter(monkeypatch, fake):
    monkeypatch.setattr(segmentation, "_rembg", lambda: fake)
    monkeypatch.setattr(segmentation, "_session", lambda model: object())


def test_segment_subject_crops_and_places_full_size(monkeypatch):
    base = Image.new("RGB", (200, 150), "white")
    base.putpixel((50, 40), (255, 0, 0))  # marker at the bbox origin
    fake = _FakeRemBg(_opaque_center)
    _patch_segmenter(monkeypatch, fake)

    alpha = segmentation.segment_subject(base, (50, 40, 120, 110), feather=0)
    assert alpha is not None
    assert alpha.size == (200, 150)
    assert alpha.mode == "L"

    # the fake received exactly the bbox crop
    assert fake.crops[0].size == (70, 70)
    assert fake.crops[0].getpixel((0, 0)) == (255, 0, 0)

    # silhouette placed at the bbox, zeros outside
    assert alpha.getpixel((0, 0)) == 0
    assert alpha.getpixel((130, 40)) == 0  # just right of bbox
    assert alpha.getpixel((50, 20)) == 0  # just above bbox
    assert alpha.getpixel((85, 75)) == 255  # dilated center


def test_segment_subject_clips_bbox_to_image_edges(monkeypatch):
    base = Image.new("RGB", (200, 150), "white")
    fake = _FakeRemBg(_opaque_center)
    _patch_segmenter(monkeypatch, fake)

    alpha = segmentation.segment_subject(base, (-20, -10, 60, 50), feather=0)
    assert alpha is not None
    assert alpha.size == (200, 150)
    # crop clipped to the in-image region (0,0)-(60,50)
    assert fake.crops[0].size == (60, 50)
    assert alpha.getpixel((30, 25)) > 0
    # nothing leaks past the clipped bbox
    assert alpha.getpixel((100, 100)) == 0
    assert alpha.getpixel((60, 25)) == 0
    assert alpha.getpixel((30, 50)) == 0


def test_segment_subject_dilates_and_softens(monkeypatch):
    base = Image.new("RGB", (100, 100), "white")

    def small(crop):
        out = Image.new("RGBA", crop.size, (0, 0, 0, 0))
        ImageDraw.Draw(out).rectangle([40, 40, 59, 59], fill=(255, 0, 0, 255))
        return out

    fake = _FakeRemBg(small)
    _patch_segmenter(monkeypatch, fake)
    raw = small(base).getchannel("A")

    alpha = segmentation.segment_subject(base, (0, 0, 100, 100), feather=20)
    assert alpha is not None
    assert segmentation.alpha_coverage(alpha) > segmentation.alpha_coverage(raw)
    assert any(0 < value < 255 for value in alpha.getdata())  # blurred edge


def test_segment_subject_degenerate_returns_none(monkeypatch):
    base = Image.new("RGB", (100, 100), "white")

    empty = _FakeRemBg(lambda crop: Image.new("RGBA", crop.size, (0, 0, 0, 0)))
    _patch_segmenter(monkeypatch, empty)
    assert segmentation.segment_subject(base, (0, 0, 100, 100), feather=8) is None

    full = _FakeRemBg(lambda crop: Image.new("RGBA", crop.size, (255, 0, 0, 255)))
    _patch_segmenter(monkeypatch, full)
    assert segmentation.segment_subject(base, (0, 0, 100, 100), feather=8) is None


def test_segment_subject_swallows_errors(monkeypatch):
    base = Image.new("RGB", (100, 100), "white")

    def boom(crop):
        raise RuntimeError("onnx exploded")

    _patch_segmenter(monkeypatch, _FakeRemBg(boom))
    assert segmentation.segment_subject(base, (0, 0, 50, 50), feather=8) is None


def test_segment_subject_empty_bbox_returns_none(monkeypatch):
    base = Image.new("RGB", (100, 100), "white")
    fake = _FakeRemBg(_opaque_center)
    _patch_segmenter(monkeypatch, fake)

    assert segmentation.segment_subject(base, (500, 500, 600, 600), feather=8) is None
    assert fake.crops == []  # rembg must not even be invoked


def test_segment_session_cache_is_per_model(monkeypatch):
    calls: list = []

    class Fake:
        def new_session(self, model):
            calls.append(model)
            return {"model": model}

    monkeypatch.setattr(segmentation, "_rembg", lambda: Fake())
    monkeypatch.setattr(segmentation, "_SESSIONS", {})

    first = segmentation._session("u2net")
    second = segmentation._session("u2net")
    assert first is second
    assert calls == ["u2net"]


# ---------------------------------------------------------------------------
# FIX 3: declared-size mismatch must fall back rather than resize/distort
# ---------------------------------------------------------------------------


def test_render_replacement_mask_silhouette_size_mismatch_falls_back(monkeypatch):
    region = _region((100, 100, 200, 200), feather=8)
    called = {"count": 0}

    def fake_segment(*args, **kwargs):
        called["count"] += 1
        return _stub_alpha((256, 256))

    monkeypatch.setattr(segmentation, "segment_subject", fake_segment)
    base = Image.new("RGB", (200, 200), "white")  # decoded size != target 256x256

    expected = render_replacement_mask(region, 256, 256, 256, 256)
    got = render_replacement_mask(
        region, 256, 256, 256, 256, shape="silhouette", base_image=base
    )
    assert got == expected
    assert called["count"] == 0  # guard trips before segmentation
