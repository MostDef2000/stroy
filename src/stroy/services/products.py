"""Product candidate service (R3 URL import / manual entry).

Persisted facts live in ``ProductCandidateRow``; descriptor columns
(``material_descriptors`` / ``color_descriptors``) are stored comma-joined in
their Text columns while the API contract exposes them as arrays of strings
(:func:`candidate_view` splits, mutators join).

Failure taxonomy: extraction/transport violations surface as
:class:`stroy.net.guard.GuardError` and map to ``422 url_rejected`` at the
API boundary; everything here is partial-friendly (an extraction that finds
no facts still persists a candidate).
"""

from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from stroy.db.models import AssetRow, ProductCandidateRow, utcnow
from stroy.domain.products import ProductCandidateFacts, UrlMetadataExtractor
from stroy.net.guard import GuardError, GuardedFetch, fetch_guarded, sniff_image_media_type
from stroy.services.assets import ObjectStore

__all__ = [
    "candidate_view",
    "create_candidate",
    "create_from_url",
    "delete_candidate",
    "extraction_status",
    "get_candidate",
    "list_candidates",
    "missing_candidate_fields",
    "patch_candidate",
    "split_descriptors",
]

ImageFetcher = Callable[[str], Awaitable[GuardedFetch]]

# Candidate facts that the owner may edit through PATCH (in extract terms:
# everything except provenance/metadata/asset links).
_USER_EDITABLE_FIELDS = (
    "title",
    "brand",
    "model",
    "price",
    "currency",
    "width_mm",
    "depth_mm",
    "height_mm",
    "material_descriptors",
    "color_descriptors",
)

# Facts reported as missing in the import-url response, mapped to their row
# columns (descriptor facts are missing when the split list is empty).
_FACT_COLUMNS = (
    ("title", "title"),
    ("brand", "brand"),
    ("model", "model"),
    ("price", "price"),
    ("currency", "currency"),
    ("width_mm", "width_mm"),
    ("depth_mm", "depth_mm"),
    ("height_mm", "height_mm"),
    ("material", "material_descriptors"),
    ("color", "color_descriptors"),
)


def split_descriptors(value: str | None) -> list[str]:
    """Comma-joined Text column → array of strings (empty → [])."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _join_descriptors(value: str | list[str] | tuple[str, ...] | None) -> str | None:
    """Array (or single string) of descriptors → comma-joined Text column."""
    if value is None:
        return None
    if isinstance(value, str):
        items = [value.strip()] if value.strip() else []
    else:
        items = [str(item).strip() for item in value if str(item).strip()]
    if not items:
        return None
    return ", ".join(items)


def candidate_view(row: ProductCandidateRow) -> dict[str, Any]:
    """API shape for a candidate (descriptors as arrays, price as float)."""
    return {
        "id": row.id,
        "project_id": row.project_id,
        "source_url": row.source_url,
        "source_asset_id": row.source_asset_id,
        "title": row.title,
        "brand": row.brand,
        "model": row.model,
        "price": float(row.price) if row.price is not None else None,
        "currency": row.currency,
        "width_mm": row.width_mm,
        "depth_mm": row.depth_mm,
        "height_mm": row.height_mm,
        "material_descriptors": split_descriptors(row.material_descriptors),
        "color_descriptors": split_descriptors(row.color_descriptors),
        "provenance": row.provenance,
        "extraction_confidence": row.extraction_confidence,
        "preview_asset_id": row.preview_asset_id,
        "three_d_ref": row.three_d_ref,
        "metadata": row.metadata_json,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def missing_candidate_fields(row: ProductCandidateRow) -> list[str]:
    """Fact names not filled in for this candidate (import-url response)."""
    missing: list[str] = []
    for fact, column in _FACT_COLUMNS:
        if column in ("material_descriptors", "color_descriptors"):
            empty = not split_descriptors(getattr(row, column))
        else:
            empty = getattr(row, column) is None
        if empty:
            missing.append(fact)
    return missing


def extraction_status(confidence: float | None) -> str:
    """``ok`` when any fact was extracted, ``empty`` for a fully blank page."""
    return "ok" if (confidence or 0) > 0 else "empty"


async def get_candidate(
    session: AsyncSession, project_id: str, candidate_id: str
) -> ProductCandidateRow | None:
    row = await session.get(ProductCandidateRow, candidate_id)
    if row is None or row.project_id != project_id:
        return None
    return row


async def create_candidate(
    session: AsyncSession,
    project_id: str,
    *,
    title: str | None = None,
    brand: str | None = None,
    model: str | None = None,
    price: float | None = None,
    currency: str | None = None,
    width_mm: float | None = None,
    depth_mm: float | None = None,
    height_mm: float | None = None,
    material_descriptors: str | list[str] | None = None,
    color_descriptors: str | list[str] | None = None,
    source_asset_id: str | None = None,
    three_d_ref: str | None = None,
) -> ProductCandidateRow:
    """Manual candidate: owner-entered facts, provenance ``manual``."""
    row = ProductCandidateRow(
        project_id=project_id,
        source_asset_id=source_asset_id,
        title=title,
        brand=brand,
        model=model,
        price=_as_price(price),
        currency=currency,
        width_mm=width_mm,
        depth_mm=depth_mm,
        height_mm=height_mm,
        material_descriptors=_join_descriptors(material_descriptors),
        color_descriptors=_join_descriptors(color_descriptors),
        provenance="manual",
        three_d_ref=three_d_ref,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def create_from_url(
    session: AsyncSession,
    project_id: str,
    url: str,
    *,
    extractor: UrlMetadataExtractor,
    object_store: ObjectStore,
    image_fetcher: ImageFetcher | None = None,
) -> ProductCandidateRow:
    """Import a candidate from a product URL.

    The extractor fetches through the SSRF guard (:class:`GuardError`
    propagates — the API maps it to 422 ``url_rejected``). Facts persist even
    when partial. The preview image, when the extractor found one, is
    downloaded through the guard into the asset store (role
    ``product_preview``); a preview failure is NON-fatal and recorded in
    ``metadata_json["preview_error"]``.
    """
    facts: ProductCandidateFacts = await extractor.extract(url)

    preview_asset_id: str | None = None
    metadata: dict[str, Any] = {}
    if facts.preview_image_url:
        fetch = image_fetcher or _default_image_fetcher
        try:
            page = await fetch(facts.preview_image_url)
        except GuardError as exc:
            metadata["preview_error"] = exc.reason
        else:
            if 200 <= page.status < 300:
                preview_asset_id = await _persist_preview_asset(
                    session,
                    project_id,
                    facts.preview_image_url,
                    page,
                    object_store,
                )
                metadata["preview_source_url"] = facts.preview_image_url
            else:
                metadata["preview_error"] = f"http_{page.status}"

    row = ProductCandidateRow(
        project_id=project_id,
        source_url=url,
        provenance="extracted",
        extraction_confidence=facts.extraction_confidence,
        title=facts.title,
        brand=facts.brand,
        model=facts.model,
        price=_as_price(facts.price),
        currency=facts.currency,
        width_mm=facts.width_mm,
        depth_mm=facts.depth_mm,
        height_mm=facts.height_mm,
        material_descriptors=_join_descriptors(facts.material),
        color_descriptors=_join_descriptors(facts.color),
        preview_asset_id=preview_asset_id,
        metadata_json=metadata,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def patch_candidate(
    session: AsyncSession,
    project_id: str,
    candidate_id: str,
    updates: dict[str, Any],
) -> ProductCandidateRow | None:
    """Apply owner edits to the user-editable facts.

    A candidate whose facts came from extraction becomes ``mixed`` provenance
    on the first user edit (manual candidates stay ``manual``). Any PATCH
    bumps ``updated_at``.
    """
    row = await get_candidate(session, project_id, candidate_id)
    if row is None:
        return None
    edited = [field for field in _USER_EDITABLE_FIELDS if field in updates]
    for field in edited:
        value = updates[field]
        if field in ("material_descriptors", "color_descriptors"):
            value = _join_descriptors(value)
        setattr(row, field, value)
    if edited and row.provenance == "extracted":
        row.provenance = "mixed"
    row.updated_at = utcnow()
    await session.commit()
    await session.refresh(row)
    return row


async def list_candidates(
    session: AsyncSession,
    project_id: str,
    *,
    source: str | None = None,
    has_dimensions: bool | None = None,
) -> list[ProductCandidateRow]:
    """Candidates for a project, newest first.

    ``source`` filters manual vs URL-imported (``url`` matches extracted and
    mixed provenance alike — both originate from an import URL).
    ``has_dimensions`` filters on the complete w/d/h triple (True) or its
    complement (False).
    """
    stmt = select(ProductCandidateRow).where(
        ProductCandidateRow.project_id == project_id
    )
    if source == "manual":
        stmt = stmt.where(ProductCandidateRow.provenance == "manual")
    elif source == "url":
        stmt = stmt.where(ProductCandidateRow.source_url.is_not(None))
    if has_dimensions is True:
        stmt = stmt.where(
            ProductCandidateRow.width_mm.is_not(None),
            ProductCandidateRow.depth_mm.is_not(None),
            ProductCandidateRow.height_mm.is_not(None),
        )
    elif has_dimensions is False:
        stmt = stmt.where(
            or_(
                ProductCandidateRow.width_mm.is_(None),
                ProductCandidateRow.depth_mm.is_(None),
                ProductCandidateRow.height_mm.is_(None),
            )
        )
    result = await session.execute(stmt.order_by(ProductCandidateRow.created_at.desc()))
    return list(result.scalars())


async def delete_candidate(
    session: AsyncSession, project_id: str, candidate_id: str
) -> bool:
    """Delete the candidate row only — referenced assets stay untouched."""
    row = await get_candidate(session, project_id, candidate_id)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


# ---------------------------------------------------------------------------
# internal helpers
# ---------------------------------------------------------------------------


async def _default_image_fetcher(url: str) -> GuardedFetch:
    return await fetch_guarded(url, expect="image")


def _as_price(value: float | None) -> Decimal | None:
    """Coerce to Decimal quantised to the Numeric(12,2) column scale."""
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


async def _persist_preview_asset(
    session: AsyncSession,
    project_id: str,
    source_url: str,
    page: GuardedFetch,
    object_store: ObjectStore,
) -> str:
    """Store a fetched preview image as a ``product_preview`` asset."""
    media = (page.content_type or "").split(";", 1)[0].strip().lower()
    if not media.startswith("image/"):
        # Header was missing/odd but the guard's magic-byte sniff passed:
        # classify by signature.
        media = sniff_image_media_type(page.body[:16]) or "application/octet-stream"
    suffix = Path(urlparse(source_url).path).suffix[:16]
    object_key = f"projects/{project_id}/{uuid4()}{suffix}"
    await object_store.put_bytes(object_key, page.body, media)
    asset = AssetRow(
        project_id=project_id,
        object_key=object_key,
        original_name=Path(urlparse(source_url).path).name[:255] or None,
        media_type=media,
        size_bytes=len(page.body),
        sha256=hashlib.sha256(page.body).hexdigest(),
        provenance="extracted",
        role="product_preview",
        metadata_json={"source_url": source_url},
    )
    session.add(asset)
    # Flush (not commit): the candidate insert commits atomically with its
    # preview asset.
    await session.flush()
    return asset.id
