from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, UnidentifiedImageError


def extract_asset_metadata(data: bytes, media_type: str) -> dict[str, Any]:
    if not media_type.startswith("image/"):
        return {}
    try:
        with Image.open(BytesIO(data)) as image:
            return {
                "width_px": image.width,
                "height_px": image.height,
                "format": image.format,
                "mode": image.mode,
            }
    except (UnidentifiedImageError, OSError):
        return {}
