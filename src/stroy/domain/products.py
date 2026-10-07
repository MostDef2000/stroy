"""Product candidate domain models (R3 URL import).

Facts are the machine-extractable attributes of a product page. Everything is
optional: an extraction may find nothing and still produce a valid (partial)
candidate — missing data is surfaced to the owner, never a hard failure.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel


class ProductCandidateFacts(BaseModel):
    """Facts extracted from a product URL (all optional, partial-friendly)."""

    title: str | None = None
    brand: str | None = None
    model: str | None = None
    price: float | None = None
    currency: str | None = None
    width_mm: float | None = None
    depth_mm: float | None = None
    height_mm: float | None = None
    material: str | None = None
    color: str | None = None
    preview_image_url: str | None = None
    extraction_confidence: float | None = None


class UrlMetadataExtractor(Protocol):
    """Contract for URL → facts extractors (the guard does the fetching).

    Implementations must fetch through :mod:`stroy.net.guard` (or a test
    seam) and return partial facts instead of raising on missing data; only
    transport/policy violations (``GuardError``) are raised.
    """

    async def extract(self, url: str) -> ProductCandidateFacts:
        ...
