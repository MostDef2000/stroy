from __future__ import annotations

import importlib
import logging
import os
import threading
from typing import Any

from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "u2net"
MIN_COVERAGE = 0.02
MAX_COVERAGE = 0.98

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
