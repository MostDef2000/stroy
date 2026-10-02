from __future__ import annotations

import importlib
import logging
import os
import threading
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageOps

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "u2net"
MIN_COVERAGE = 0.02
MAX_COVERAGE = 0.98
# A real subject silhouette must cover every sub-region of the bbox: 4 stacked
# horizontal bands AND 4 left-to-right vertical strips. Measured on the failed
# v20 production mask (bbox [334,578,654,884]): top band = 0.086, minimum strip
# = 0.338, so 0.15 keeps a firm margin over the observed failure while genuine
# full-chair masks stay well above it.
BAND_COVERAGE_MIN = 0.15

# rembg sessions hold the loaded ONNX model; keep one per model name so a
# request burst does not reload the network for every replacement mask.
_SESSIONS: dict[str, Any] = {}
_SESSIONS_LOCK = threading.Lock()


def _resolve_model(model: str | None) -> str:
    return model or os.environ.get("STROY_SEGMENTATION_MODEL") or DEFAULT_MODEL


def _rembg() -> Any:
    # Lazy import: a missing/broken rembg (or onnxruntime) must never break
    # importing the editing package.
    return importlib.import_module("rembg")


def _cv2() -> Any:
    # Lazy import: OpenCV/NumPy are an optional refinement path and must not
    # become hard module-level dependencies of the editing package.
    return importlib.import_module("cv2")


def _numpy() -> Any:
    return importlib.import_module("numpy")


def _session(model: str) -> Any:
    session = _SESSIONS.get(model)
    if session is None:
        with _SESSIONS_LOCK:
            # Double-checked: another thread may have built it while waiting.
            session = _SESSIONS.get(model)
            if session is None:
                session = _rembg().new_session(model)
                _SESSIONS[model] = session
    return session


def alpha_coverage(alpha: Image.Image) -> float:
    """Fraction of non-zero pixels in ``alpha`` (0.0 = empty, 1.0 = full)."""
    total = alpha.width * alpha.height
    if total <= 0:
        return 0.0
    histogram = alpha.histogram()
    filled = sum(histogram[1:])
    return filled / total


def is_degenerate_alpha(
    alpha: Image.Image,
    *,
    min_coverage: float = MIN_COVERAGE,
    max_coverage: float = MAX_COVERAGE,
) -> bool:
    """True when the silhouette covers almost nothing or almost everything.

    Both cases mean U2Net did not find a usable subject inside the box, so the
    caller should fall back to the rectangle mask.
    """
    coverage = alpha_coverage(alpha)
    return coverage < min_coverage or coverage > max_coverage


def _region_coverage(alpha: Image.Image, box: tuple[int, int, int, int]) -> float:
    """Fraction of pixels above mid-gray inside ``box`` (already clamped)."""
    region = alpha.crop(box)
    total = region.width * region.height
    if total == 0:
        return 0.0
    return sum(region.histogram()[129:]) / total


def is_partial_silhouette(alpha: Image.Image, bbox: tuple[int, int, int, int]) -> bool:
    """True when the silhouette is suspiciously partial (e.g. a half chair).

    Only the pixels inside ``bbox`` are considered. The box is split into four
    stacked horizontal bands AND four left-to-right vertical strips; if any of
    the eight sub-regions has less than ``BAND_COVERAGE_MIN`` of its pixels
    above mid-gray, the silhouette is treated as partial so the caller can fall
    back to the rectangle mask.
    """
    x0, y0, x1, y1 = (int(value) for value in bbox)
    x0 = max(0, min(x0, alpha.width))
    x1 = max(0, min(x1, alpha.width))
    y0 = max(0, min(y0, alpha.height))
    y1 = max(0, min(y1, alpha.height))
    if x1 <= x0 or y1 <= y0:
        return True

    width = x1 - x0
    height = y1 - y0
    for band in range(4):
        band_box = (x0, y0 + (height * band) // 4, x1, y0 + (height * (band + 1)) // 4)
        if band_box[3] <= band_box[1] or _region_coverage(alpha, band_box) < BAND_COVERAGE_MIN:
            return True
    for strip in range(4):
        strip_box = (x0 + (width * strip) // 4, y0, x0 + (width * (strip + 1)) // 4, y1)
        if strip_box[2] <= strip_box[0] or _region_coverage(alpha, strip_box) < BAND_COVERAGE_MIN:
            return True
    return False


def _refine_with_grabcut(
    crop: Image.Image,
    alpha: Image.Image,
    pad: int,
) -> Image.Image | None:
    """Refine a rembg alpha with GrabCut, seeded by the alpha itself.

    The crop is padded by ``pad`` px (black fill; the outer ring is forced to
    sure-background anyway) and GrabCut runs in ``GC_INIT_WITH_MASK`` mode:
    alpha > 200 seeds sure-foreground, and the outer ring of thickness
    ``max(4, pad // 2)`` is sure-background. Everything else starts as
    probable background, which lets GrabCut recover low-contrast regions
    connected to the seed.

    Returns an L-mode alpha cropped back to the original crop size, or ``None``
    on any failure (OpenCV/NumPy missing, GrabCut error). Never raises.
    """
    try:
        cv2 = _cv2()
        np = _numpy()
    except ModuleNotFoundError:
        # Expected in environments without OpenCV; refinement is optional.
        logger.info("OpenCV not installed; grabCut refinement unavailable")
        return None
    except Exception:  # noqa: BLE001 - a broken install must not break the flow
        logger.warning("grabCut refinement failed; using raw alpha", exc_info=True)
        return None

    try:
        padded = ImageOps.expand(crop, border=pad, fill=0)
        padded_w, padded_h = padded.size

        mat_img = Image.new("L", (padded_w, padded_h), cv2.GC_PR_BGD)
        ring = max(4, pad // 2)
        ImageDraw.Draw(mat_img).rectangle(
            [0, 0, padded_w - 1, padded_h - 1], outline=cv2.GC_BGD, width=ring
        )
        fg_seed = alpha.point(lambda value: 255 if value > 200 else 0)
        mat_img.paste(cv2.GC_FGD, (pad, pad), fg_seed)

        mat = np.array(mat_img)
        img_bgr = np.asarray(padded)[:, :, ::-1].copy()
        bgd_model = np.zeros((1, 65), dtype=np.float64)
        fgd_model = np.zeros((1, 65), dtype=np.float64)
        cv2.grabCut(
            img_bgr,
            mat,
            None,
            bgd_model,
            fgd_model,
            5,
            cv2.GC_INIT_WITH_MASK,
        )

        keep = (mat == cv2.GC_FGD) | (mat == cv2.GC_PR_FGD)
        arr = (keep[pad : pad + crop.height, pad : pad + crop.width] * 255).astype(
            np.uint8
        )
        return Image.fromarray(arr, "L")
    except Exception:  # noqa: BLE001 - refinement is best-effort
        logger.warning("grabCut refinement failed; using raw alpha", exc_info=True)
        return None


def segment_subject(
    image: Image.Image,
    bbox: tuple[int, int, int, int],
    model: str | None = None,
    *,
    feather: int = 0,
) -> Image.Image | None:
    """Render a soft subject-silhouette alpha for ``bbox`` (x0, y0, x1, y1).

    The image is cropped to ``bbox``, rembg (U2Net by default) extracts the
    subject, and the alpha is dilated by ``max(8, feather//2)`` px and blurred
    by ``feather/2`` for a soft edge. The result is a full-size L-mode image
    with the silhouette placed at the bbox location (zeros elsewhere, clipped
    to the image bounds).

    Returns ``None`` on any failure or a degenerate result; never raises.
    """
    try:
        x0, y0, x1, y1 = (int(value) for value in bbox)
        width, height = image.size
        cx0 = max(0, min(x0, width))
        cy0 = max(0, min(y0, height))
        cx1 = max(0, min(x1, width))
        cy1 = max(0, min(y1, height))
        if cx1 <= cx0 or cy1 <= cy0:
            logger.warning("segmentation bbox empty after clipping: %s", bbox)
            return None

        crop = image.crop((cx0, cy0, cx1, cy1))
        cutout = _rembg().remove(crop, session=_session(_resolve_model(model)))
        if cutout.mode == "RGBA":
            alpha = cutout.getchannel("A")
        else:
            alpha = cutout.convert("L")

        if is_degenerate_alpha(alpha):
            logger.warning(
                "segmentation produced a degenerate silhouette (coverage=%.3f); "
                "falling back to rectangle",
                alpha_coverage(alpha),
            )
            return None

        working = alpha
        try:
            refined = _refine_with_grabcut(crop, alpha, max(16, int(feather)))
        except Exception:  # noqa: BLE001 - never let refinement break the flow
            logger.warning("grabCut refinement raised; using raw alpha", exc_info=True)
            refined = None
        if refined is not None:
            working = refined
        else:
            logger.info("grabCut refinement skipped; using raw rembg alpha")

        # Guard before dilation: a dilated half-mask must not hide the defect.
        if is_partial_silhouette(working, (0, 0, working.width, working.height)):
            logger.warning("partial silhouette detected; falling back to rectangle")
            return None

        alpha = working
        dilate_px = max(8, int(feather) // 2)
        alpha = alpha.filter(ImageFilter.MaxFilter(2 * dilate_px + 1))
        blur_radius = max(0.0, int(feather) / 2.0)
        if blur_radius > 0:
            alpha = alpha.filter(ImageFilter.GaussianBlur(radius=blur_radius))

        full = Image.new("L", (width, height), 0)
        full.paste(alpha, (cx0, cy0))
        return full
    except Exception:  # noqa: BLE001 - segmentation must never break the mask flow
        logger.warning("subject segmentation failed; falling back to rectangle", exc_info=True)
        return None
