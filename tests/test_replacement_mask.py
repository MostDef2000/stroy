"""Server-side replacement mask rasterization (feather baked into the PNG)."""

import io
import types

import pytest
from PIL import Image, ImageDraw, ImageFilter

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
    # Centered rectangle spans all four bands AND all four strips, so the
    # bidirectional partial-silhouette guard accepts it; these tests target
    # placement/dilation, not shape.
    out = Image.new("RGBA", crop.size, (255, 0, 0, 0))
    w, h = crop.size
    mx = max(1, w * 15 // 100)
    my = max(1, h * 5 // 100)
    ImageDraw.Draw(out).rectangle(
        [mx, my, w - 1 - mx, h - 1 - my], fill=(255, 0, 0, 255)
    )
    return out


def _patch_segmenter(monkeypatch, fake):
    monkeypatch.setattr(segmentation, "_rembg", lambda: fake)
    monkeypatch.setattr(segmentation, "_session", lambda model: object())


def test_segment_subject_crops_and_places_full_size(monkeypatch):
    base = Image.new("RGB", (200, 150), "white")
    base.putpixel((50, 40), (255, 0, 0))  # marker at the bbox origin
    fake = _FakeRemBg(_opaque_center)
    _patch_segmenter(monkeypatch, fake)
    # Deterministic raw dilate/blur path regardless of cv2 availability.
    monkeypatch.setattr(segmentation, "_refine_with_grabcut", lambda *a, **k: None)

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
    # Deterministic raw dilate/blur path regardless of cv2 availability.
    monkeypatch.setattr(segmentation, "_refine_with_grabcut", lambda *a, **k: None)

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
        ImageDraw.Draw(out).rectangle([15, 15, 84, 84], fill=(255, 0, 0, 255))
        return out

    fake = _FakeRemBg(small)
    _patch_segmenter(monkeypatch, fake)
    # Force the raw dilate+blur path: with real cv2 installed the grabCut
    # refinement would replace this output and make the test env-dependent.
    monkeypatch.setattr(segmentation, "_refine_with_grabcut", lambda *a, **k: None)
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


# ---------------------------------------------------------------------------
# UPGRADE 1: GrabCut refinement (fake cv2 + numpy; no real OpenCV/network)
# ---------------------------------------------------------------------------


class _FakeArray:
    """Minimal ndarray lookalike for the grabCut helper tests."""

    def __init__(self, data):
        self.data = data

    @classmethod
    def from_image(cls, image):
        width, height = image.size
        if image.mode == "RGB":
            return cls(
                [[[*image.getpixel((x, y))] for x in range(width)] for y in range(height)]
            )
        return cls([[image.getpixel((x, y)) for x in range(width)] for y in range(height)])

    @classmethod
    def zeros(cls, shape):
        height, width = shape
        return cls([[0] * width for _ in range(height)])

    @property
    def height(self):
        return len(self.data)

    @property
    def width(self):
        return len(self.data[0])

    def __getitem__(self, key):
        if (
            isinstance(key, tuple)
            and len(key) == 3
            and isinstance(key[2], slice)
            and key[2].step == -1
        ):
            return _FakeArray(
                [[list(reversed(pixel)) for pixel in row] for row in self.data]
            )
        if isinstance(key, tuple) and len(key) == 2:
            if isinstance(key[0], slice) and isinstance(key[1], slice):
                rows = range(*key[0].indices(self.height))
                cols = range(*key[1].indices(self.width))
                return _FakeArray([[self.data[y][x] for x in cols] for y in rows])
            return self.data[key[0]][key[1]]
        raise TypeError(key)

    def __setitem__(self, key, value):
        if isinstance(key, tuple) and len(key) == 2:
            self.data[key[0]][key[1]] = value
            return
        raise TypeError(key)

    def _map(self, fn, other=None):
        out = []
        for y, row in enumerate(self.data):
            other_row = other.data[y] if isinstance(other, _FakeArray) else other
            out.append(
                [
                    fn(value, other_row[x] if isinstance(other_row, list) else other_row)
                    for x, value in enumerate(row)
                ]
            )
        return _FakeArray(out)

    def __eq__(self, other):
        return self._map(lambda a, b: a == b, other)

    def __or__(self, other):
        return self._map(lambda a, b: bool(a) or bool(b), other)

    def __mul__(self, other):
        return self._map(lambda a, b: a * b, other)

    def astype(self, dtype):
        return self

    def copy(self):
        return _FakeArray([list(row) for row in self.data])


class _FakeNumpy:
    uint8 = "uint8"
    float64 = "float64"

    @staticmethod
    def array(image):
        return _FakeArray.from_image(image)

    @staticmethod
    def asarray(image):
        return _FakeArray.from_image(image)

    @staticmethod
    def zeros(shape, dtype=None):
        return _FakeArray.zeros(shape)


def _fake_cv2(captured):
    def grabcut(img, mat, rect, bgd, fgd, iterations, mode):
        captured["img"] = img
        captured["mat"] = mat
        captured["iterations"] = iterations
        captured["mode"] = mode
        for y in range(mat.height):
            for x in range(mat.width):
                if mat[y, x] == 2 and sum(img[y, x]) < 300:  # PR_BGD + dark pixel
                    mat[y, x] = 3  # PR_FGD

    return types.SimpleNamespace(
        GC_BGD=0,
        GC_FGD=1,
        GC_PR_BGD=2,
        GC_PR_FGD=3,
        GC_INIT_WITH_MASK=1,
        grabCut=grabcut,
    )


def test_refine_with_grabcut_seeds_and_strips_pad(monkeypatch):
    crop = Image.new("RGB", (60, 60), "white")
    ImageDraw.Draw(crop).rectangle([5, 5, 14, 14], fill=(0, 0, 0))
    crop.putpixel((45, 45), (10, 20, 30))  # distinct channels for the BGR check
    alpha = Image.new("L", (60, 60), 0)
    ImageDraw.Draw(alpha).rectangle([30, 30, 39, 39], fill=255)

    captured: dict = {}
    monkeypatch.setattr(segmentation, "_cv2", lambda: _fake_cv2(captured))
    monkeypatch.setattr(segmentation, "_numpy", lambda: _FakeNumpy())

    def fake_fromarray(arr, mode=None):
        data = bytes(value for row in arr.data for value in row)
        return Image.frombytes(mode or "L", (arr.width, arr.height), data)

    monkeypatch.setattr(segmentation.Image, "fromarray", fake_fromarray)

    pad = 16
    refined = segmentation._refine_with_grabcut(crop, alpha, pad)
    assert refined is not None
    assert refined.size == (60, 60)
    assert refined.mode == "L"

    mat = captured["mat"]
    assert captured["mode"] == 1
    assert captured["iterations"] == 5
    # sure-foreground seeded from alpha > 200 at the pad offset
    assert mat[pad + 35, pad + 35] == 1
    # sure-background ring occupies the outer border
    assert mat[0, 0] == 0
    assert mat[7, 7] == 0
    # an untouched white crop pixel stays probable background
    assert mat[pad + 50, pad + 50] == 2
    # the fake grabCut promoted the dark region to probable foreground
    assert mat[pad + 10, pad + 10] == 3

    # pad border stripped; seeded/promoted kept, background dropped
    assert refined.getpixel((35, 35)) == 255
    assert refined.getpixel((10, 10)) == 255
    assert refined.getpixel((50, 50)) == 0
    # BGR conversion reached cv2: RGB (10, 20, 30) -> BGR (30, 20, 10)
    assert captured["img"][pad + 45, pad + 45] == [30, 20, 10]
    assert captured["img"][pad + 10, pad + 10] == [0, 0, 0]


def test_refine_with_grabcut_returns_none_without_opencv(monkeypatch):
    crop = Image.new("RGB", (30, 30), "white")
    alpha = Image.new("L", (30, 30), 0)

    # Simulate a missing OpenCV deterministically, independent of whether the
    # real cv2 package happens to be installed in the environment.
    def missing_cv2():
        raise ModuleNotFoundError("No module named 'cv2'")

    monkeypatch.setattr(segmentation, "_cv2", missing_cv2)
    assert segmentation._refine_with_grabcut(crop, alpha, 16) is None


# ---------------------------------------------------------------------------
# UPGRADE 2: band guard
# ---------------------------------------------------------------------------


def test_is_partial_silhouette_full_chair_not_partial():
    alpha = Image.new("L", (100, 200), 0)
    ImageDraw.Draw(alpha).ellipse([2, 2, 97, 197], fill=255)
    assert segmentation.is_partial_silhouette(alpha, (0, 0, 100, 200)) is False


def test_is_partial_silhouette_half_chair_is_partial():
    # v20 failure shape: bottom-heavy blob, top band empty
    alpha = Image.new("L", (100, 200), 0)
    ImageDraw.Draw(alpha).ellipse([10, 100, 90, 195], fill=255)
    assert segmentation.is_partial_silhouette(alpha, (0, 0, 100, 200)) is True


def test_is_partial_silhouette_height_sliver_is_partial():
    alpha = Image.new("L", (100, 40), 0)
    ImageDraw.Draw(alpha).rectangle([10, 0, 90, 4], fill=255)  # top band only
    assert segmentation.is_partial_silhouette(alpha, (0, 0, 100, 40)) is True


def test_is_partial_silhouette_threshold_boundary():
    # 100x100 with only the top band thinned: 25 rows * 100 cols = 2500 px.
    pass_alpha = Image.new("L", (100, 100), 255)
    ImageDraw.Draw(pass_alpha).rectangle([15, 0, 99, 24], fill=0)  # 15 cols left = 15%
    assert segmentation.is_partial_silhouette(pass_alpha, (0, 0, 100, 100)) is False

    fail_alpha = Image.new("L", (100, 100), 255)
    ImageDraw.Draw(fail_alpha).rectangle([14, 0, 99, 24], fill=0)  # 14 cols left = 14%
    assert segmentation.is_partial_silhouette(fail_alpha, (0, 0, 100, 100)) is True


# ---------------------------------------------------------------------------
# UPGRADE 1+2 pipeline
# ---------------------------------------------------------------------------


def _full_chair_rembg(crop):
    # Centered rectangle spans every band and strip (convex, ~75% coverage).
    out = Image.new("RGBA", crop.size, (255, 0, 0, 0))
    w, h = crop.size
    mx = max(2, w * 12 // 100)
    my = max(2, h * 12 // 100)
    ImageDraw.Draw(out).rectangle(
        [mx, my, w - 1 - mx, h - 1 - my], fill=(255, 0, 0, 255)
    )
    return out


def _half_chair_rembg(crop):
    out = Image.new("RGBA", crop.size, (255, 0, 0, 0))
    ImageDraw.Draw(out).ellipse(
        [crop.width // 5, crop.height // 2, crop.width * 4 // 5, crop.height - 3],
        fill=(255, 0, 0, 255),
    )
    return out


def test_segment_subject_full_chair_passes_through(monkeypatch):
    base = Image.new("RGB", (120, 120), "white")
    _patch_segmenter(monkeypatch, _FakeRemBg(_full_chair_rembg))
    monkeypatch.setattr(segmentation, "_refine_with_grabcut", lambda *a, **k: None)

    feather = 12
    out = segmentation.segment_subject(base, (0, 0, 120, 120), feather=feather)
    assert out is not None
    raw = _full_chair_rembg(base).getchannel("A")
    expected = raw.filter(ImageFilter.MaxFilter(2 * max(8, feather // 2) + 1)).filter(
        ImageFilter.GaussianBlur(radius=feather / 2)
    )
    assert list(out.getdata()) == list(expected.getdata())


def test_segment_subject_half_chair_returns_none(monkeypatch):
    # Refinement unavailable (so the guard applies even when skipped): the
    # bottom-heavy half mask must still be rejected.
    base = Image.new("RGB", (120, 120), "white")
    _patch_segmenter(monkeypatch, _FakeRemBg(_half_chair_rembg))
    monkeypatch.setattr(segmentation, "_refine_with_grabcut", lambda *a, **k: None)
    assert segmentation.segment_subject(base, (0, 0, 120, 120), feather=8) is None


def test_segment_subject_horizontal_half_chair_returns_none(monkeypatch):
    # Left half only: all horizontal bands are ~50%, but the right-most
    # vertical strip is empty -> the bidirectional guard rejects it.
    base = Image.new("RGB", (120, 120), "white")

    def left_half(crop):
        out = Image.new("RGBA", crop.size, (255, 0, 0, 0))
        ImageDraw.Draw(out).rectangle(
            [0, 0, crop.width // 2, crop.height - 1], fill=(255, 0, 0, 255)
        )
        return out

    _patch_segmenter(monkeypatch, _FakeRemBg(left_half))
    monkeypatch.setattr(segmentation, "_refine_with_grabcut", lambda *a, **k: None)
    assert segmentation.segment_subject(base, (0, 0, 120, 120), feather=8) is None


def test_refinement_recovers_partial_raw_mask(monkeypatch):
    # Raw rembg output is a half chair, but the refined mask is a full chair:
    # the band guard must judge the REFINED mask, so it passes here.
    base = Image.new("RGB", (120, 120), "white")
    _patch_segmenter(monkeypatch, _FakeRemBg(_half_chair_rembg))
    refined = _full_chair_rembg(base).getchannel("A")
    monkeypatch.setattr(segmentation, "_refine_with_grabcut", lambda *a, **k: refined)

    out = segmentation.segment_subject(base, (0, 0, 120, 120), feather=8)
    assert out is not None
    expected = refined.filter(ImageFilter.MaxFilter(17)).filter(
        ImageFilter.GaussianBlur(radius=4)
    )
    assert list(out.getdata()) == list(expected.getdata())


def test_refinement_raising_uses_raw_alpha(monkeypatch):
    base = Image.new("RGB", (120, 120), "white")
    _patch_segmenter(monkeypatch, _FakeRemBg(_full_chair_rembg))

    def boom(*args, **kwargs):
        raise RuntimeError("cv2 exploded")

    monkeypatch.setattr(segmentation, "_refine_with_grabcut", boom)
    out = segmentation.segment_subject(base, (0, 0, 120, 120), feather=8)
    assert out is not None
    assert out.getpixel((60, 60)) == 255


def test_render_falls_back_when_half_chair(monkeypatch):
    region = _region((0, 0, 120, 120), feather=8)
    _patch_segmenter(monkeypatch, _FakeRemBg(_half_chair_rembg))
    monkeypatch.setattr(segmentation, "_refine_with_grabcut", lambda *a, **k: None)

    base = Image.new("RGB", (120, 120), "white")
    expected = render_replacement_mask(region, 120, 120, 120, 120)
    got = render_replacement_mask(
        region, 120, 120, 120, 120, shape="silhouette", base_image=base
    )
    assert got == expected
