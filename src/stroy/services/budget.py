"""Variant budget service (R4).

Budget lines are bound to ONE variant (``variant_id``) and, at creation, to
that variant's head scene revision (``scene_revision_id`` — an explicit
binding that never re-resolves to "latest"). Money rules:

- ``amount`` present ⇒ ``currency`` required; when the caller omits it the
  service defaults to RUB (owner decision, 2026-10-07);
- ``kind="candidate"`` must reference a ``product_candidates`` row of the
  same project (real FK + project check);
- ``kind="material"`` carries takeoff parameters in ``metadata_json["takeoff"]``
  and gets a deterministic server-side quantity/subtotal computation
  (waste factor, package size → package count). Missing inputs leave the item
  incomplete with item-level unknowns — never zero-coerced.

The report is bound to the variant's HEAD revision: geometry takeoff reads
that revision's scene snapshot only.
"""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stroy.db.models import BudgetItemRow, ProductCandidateRow, SceneRevisionRow, SceneVariantRow
from stroy.domain.models import EntityKind, Scene
from stroy.services.geometry_quantities import (
    baseboard_length_mm,
    ceiling_area_mm2,
    floor_area_mm2,
    wall_area_mm2,
    wall_length_mm,
)

__all__ = [
    "BudgetError",
    "add_budget_item",
    "budget_item_view",
    "budget_report",
    "delete_budget_item",
    "get_budget_item",
    "list_budget_items",
    "patch_budget_item",
]

BUDGET_KINDS = {"candidate", "lighting", "manual", "material"}
COVERAGE_UNITS = {"m2", "linear_m", "each"}
DEFAULT_CURRENCY = "RUB"
# mm² → m² / mm → m
_MM2_PER_M2 = 1_000_000.0
_MM_PER_M = 1000.0


class BudgetError(ValueError):
    """Service-level budget failure with an API mapping."""

    def __init__(self, detail: str, *, status_code: int = 422, code: str = "budget_error"):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.code = code


def budget_item_view(row: BudgetItemRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "variant_id": row.variant_id,
        "scene_revision_id": row.scene_revision_id,
        "kind": row.kind,
        "product_candidate_id": row.product_candidate_id,
        "label": row.label,
        "amount": float(row.amount) if row.amount is not None else None,
        "currency": row.currency,
        "quantity": row.quantity,
        "metadata": row.metadata_json,
        "created_at": row.created_at,
    }


def _as_amount(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise BudgetError(f"invalid amount: {value!r}", code="invalid_amount") from exc
    if not amount.is_finite():
        raise BudgetError(f"invalid amount: {value!r}", code="invalid_amount")
    if amount < 0:
        raise BudgetError("amount must be >= 0", code="invalid_amount")
    return amount.quantize(Decimal("0.01"))


def _validate_currency(currency: Any) -> str | None:
    if currency is None:
        return None
    if not isinstance(currency, str) or not currency.strip() or len(currency) > 12:
        raise BudgetError(
            "currency must be a non-empty string of at most 12 characters",
            code="invalid_currency",
        )
    return currency.strip()


def _validate_takeoff(kind: str, metadata: dict[str, Any]) -> None:
    if kind != "material":
        return
    takeoff = metadata.get("takeoff")
    if not isinstance(takeoff, dict):
        raise BudgetError(
            'material items require metadata["takeoff"] parameters',
            code="invalid_takeoff",
        )
    unit = takeoff.get("coverage_unit")
    if unit not in COVERAGE_UNITS:
        raise BudgetError(
            f'takeoff.coverage_unit must be one of {sorted(COVERAGE_UNITS)}',
            code="invalid_takeoff",
        )
    for key in ("waste_factor", "package_size", "unit_price"):
        value = takeoff.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise BudgetError(f"takeoff.{key} must be a number", code="invalid_takeoff")
        if value < 0:
            raise BudgetError(f"takeoff.{key} must be >= 0", code="invalid_takeoff")


def _validate_payload_fields(payload: dict[str, Any]) -> None:
    unknown = set(payload) - {
        "kind",
        "product_candidate_id",
        "label",
        "amount",
        "currency",
        "quantity",
        "metadata",
    }
    if unknown:
        raise BudgetError(
            f"unknown budget item fields: {', '.join(sorted(unknown))}",
            code="invalid_payload",
        )


async def get_budget_item(
    session: AsyncSession, project_id: str, variant_id: str, item_id: str
) -> BudgetItemRow | None:
    row = await session.get(BudgetItemRow, item_id)
    if row is None or row.project_id != project_id or row.variant_id != variant_id:
        return None
    return row


async def add_budget_item(
    session: AsyncSession, project_id: str, variant_id: str, payload: dict[str, Any]
) -> BudgetItemRow | None:
    _validate_payload_fields(payload)
    variant = await session.get(SceneVariantRow, variant_id)
    if variant is None or variant.project_id != project_id:
        return None

    kind = payload.get("kind")
    if kind not in BUDGET_KINDS:
        raise BudgetError(
            f"kind must be one of {sorted(BUDGET_KINDS)}, got: {kind}",
            code="invalid_kind",
        )
    candidate_id = payload.get("product_candidate_id")
    if kind == "candidate":
        if not candidate_id:
            raise BudgetError(
                'kind="candidate" requires product_candidate_id',
                code="candidate_required",
            )
        candidate = await session.get(ProductCandidateRow, candidate_id)
        if candidate is None or candidate.project_id != project_id:
            raise BudgetError(
                f"product candidate not found in project: {candidate_id}",
                code="unknown_candidate",
            )
    elif candidate_id is not None:
        raise BudgetError(
            'product_candidate_id is only valid for kind="candidate"',
            code="candidate_not_allowed",
        )

    label = payload.get("label")
    if not isinstance(label, str) or not label.strip() or len(label) > 200:
        raise BudgetError(
            "label must be a non-empty string of at most 200 characters",
            code="invalid_label",
        )

    amount = _as_amount(payload.get("amount"))
    currency = _validate_currency(payload.get("currency"))
    if amount is not None and currency is None:
        # Owner decision: new budget items default to RUB.
        currency = DEFAULT_CURRENCY

    quantity = payload.get("quantity")
    if quantity is not None:
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
            raise BudgetError("quantity must be a number", code="invalid_quantity")
        if quantity < 0:
            raise BudgetError("quantity must be >= 0", code="invalid_quantity")

    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise BudgetError("metadata must be an object", code="invalid_metadata")
    _validate_takeoff(kind, metadata)

    row = BudgetItemRow(
        project_id=project_id,
        variant_id=variant_id,
        # Explicit binding: the variant head at creation time.
        scene_revision_id=variant.head_scene_revision_id,
        kind=kind,
        product_candidate_id=candidate_id,
        label=label.strip(),
        amount=amount,
        currency=currency,
        quantity=quantity,
        metadata_json=metadata,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def patch_budget_item(
    session: AsyncSession,
    project_id: str,
    variant_id: str,
    item_id: str,
    updates: dict[str, Any],
) -> BudgetItemRow | None:
    row = await get_budget_item(session, project_id, variant_id, item_id)
    if row is None:
        return None
    _validate_payload_fields(updates)
    if "kind" in updates and updates["kind"] != row.kind:
        raise BudgetError("kind is immutable", code="kind_immutable")
    if "metadata" in updates:
        if not isinstance(updates["metadata"], dict):
            raise BudgetError("metadata must be an object", code="invalid_metadata")
        row.metadata_json = updates["metadata"]
    if "label" in updates:
        label = updates["label"]
        if not isinstance(label, str) or not label.strip() or len(label) > 200:
            raise BudgetError(
                "label must be a non-empty string of at most 200 characters",
                code="invalid_label",
            )
        row.label = label.strip()
    if "amount" in updates:
        row.amount = _as_amount(updates["amount"])
    if "currency" in updates:
        row.currency = _validate_currency(updates["currency"])
    if "quantity" in updates:
        quantity = updates["quantity"]
        if quantity is not None:
            if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
                raise BudgetError("quantity must be a number", code="invalid_quantity")
            if quantity < 0:
                raise BudgetError("quantity must be >= 0", code="invalid_quantity")
        row.quantity = quantity
    # Re-apply the currency invariant after the patch.
    if row.amount is not None and row.currency is None:
        row.currency = DEFAULT_CURRENCY
    await session.commit()
    await session.refresh(row)
    return row


async def delete_budget_item(
    session: AsyncSession, project_id: str, variant_id: str, item_id: str
) -> bool:
    row = await get_budget_item(session, project_id, variant_id, item_id)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


async def list_budget_items(
    session: AsyncSession, project_id: str, variant_id: str
) -> list[BudgetItemRow]:
    """Items of one variant in entry order (bill of quantities)."""
    result = await session.execute(
        select(BudgetItemRow)
        .where(
            BudgetItemRow.project_id == project_id,
            BudgetItemRow.variant_id == variant_id,
        )
        .order_by(BudgetItemRow.created_at.asc(), BudgetItemRow.id.asc())
    )
    return list(result.scalars())


def _material_takeoff(
    item: BudgetItemRow, scene: Scene
) -> tuple[dict[str, Any] | None, list[str]]:
    """Deterministic takeoff for a material item.

    Returns (takeoff, unknowns). ``takeoff`` is None when the quantity could
    not be derived; unknowns explain why.
    """
    takeoff = item.metadata_json.get("takeoff")
    if not isinstance(takeoff, dict):
        return None, ["missing_quantity"]
    unit = takeoff.get("coverage_unit")
    target_id = takeoff.get("target_id")
    waste = takeoff.get("waste_factor", 0.0)
    package_size = takeoff.get("package_size")
    unit_price = takeoff.get("unit_price")

    unknowns: list[str] = []
    base_quantity: float | None = None
    entity = next((e for e in scene.entities if e.id == target_id), None) if target_id else None

    if unit == "each":
        # Count-based: the explicit item quantity is the takeoff.
        base_quantity = item.quantity
        if base_quantity is None:
            unknowns.append("missing_quantity")
    elif unit == "m2":
        if entity is not None and entity.kind is EntityKind.FLOOR:
            area = floor_area_mm2(entity)
            base_quantity = area / _MM2_PER_M2 if area is not None else None
        elif entity is not None and entity.kind is EntityKind.CEILING:
            area = ceiling_area_mm2(entity)
            base_quantity = area / _MM2_PER_M2 if area is not None else None
        elif entity is not None and entity.kind is EntityKind.ROOM:
            area = wall_area_mm2(scene, entity.id)
            base_quantity = area / _MM2_PER_M2 if area is not None else None
        if base_quantity is None:
            unknowns.append("missing_quantity")
    elif unit == "linear_m":
        if entity is not None and entity.kind is EntityKind.ROOM:
            length = baseboard_length_mm(scene, entity.id)
            base_quantity = length / _MM_PER_M if length is not None else None
        elif entity is not None and entity.kind is EntityKind.WALL:
            length = wall_length_mm(entity)
            base_quantity = length / _MM_PER_M if length is not None else None
        if base_quantity is None:
            unknowns.append("missing_quantity")
    else:
        unknowns.append("missing_quantity")

    if unknowns or base_quantity is None:
        return None, sorted(set(unknowns))

    required = base_quantity * (1.0 + float(waste or 0.0))
    if package_size is not None and float(package_size) > 0:
        package_count: float = float(math.ceil(required / float(package_size)))
    else:
        package_count = required
    takeoff_view = {
        "coverage_unit": unit,
        "target_id": target_id,
        "waste_factor": float(waste or 0.0),
        "package_size": float(package_size) if package_size is not None else None,
        "base_quantity": base_quantity,
        "required_quantity": required,
        "package_count": package_count,
    }
    if unit_price is None:
        return takeoff_view, ["missing_price"]
    takeoff_view["unit_price"] = float(unit_price)
    takeoff_view["subtotal"] = round(package_count * float(unit_price), 2)
    return takeoff_view, []


def _price_item(
    item: BudgetItemRow, scene: Scene, candidates: dict[str, ProductCandidateRow | None]
) -> dict[str, Any]:
    """Compute one item's effective price/quantity/currency and contribution."""
    unknowns: list[str] = []
    effective_amount: float | None = None
    effective_currency: str | None = item.currency
    effective_quantity: float | None = item.quantity
    takeoff: dict[str, Any] | None = None

    if item.kind == "material" and item.amount is None:
        takeoff, takeoff_unknowns = _material_takeoff(item, scene)
        unknowns.extend(takeoff_unknowns)
        if takeoff is not None and "subtotal" in takeoff:
            effective_amount = takeoff["subtotal"]
            effective_quantity = takeoff["required_quantity"]
    elif item.kind == "candidate" and item.amount is None:
        candidate = candidates.get(item.product_candidate_id or "")
        if candidate is None or candidate.price is None:
            unknowns.append("missing_price")
        else:
            effective_amount = float(candidate.price)
            if effective_currency is None:
                effective_currency = candidate.currency
    else:
        if item.amount is None:
            unknowns.append("missing_price")
        else:
            effective_amount = float(item.amount)
    if effective_amount is not None and item.kind == "material" and takeoff is not None:
        effective_quantity = takeoff["required_quantity"]
    if effective_amount is not None and effective_currency is None:
        unknowns.append("missing_currency")
    if effective_amount is not None and effective_quantity is None:
        # No explicit quantity and no takeoff: one unit by default.
        effective_quantity = 1.0

    contribution: float | None = None
    if effective_amount is not None and effective_quantity is not None and not unknowns:
        if item.kind == "material" and takeoff is not None:
            # The takeoff subtotal already prices the full required quantity
            # (waste + packaging included) — multiplying again would
            # double-count (e.g. 9 packages × 8010).
            contribution = round(effective_amount, 2)
        else:
            contribution = round(effective_amount * effective_quantity, 2)

    return {
        "id": item.id,
        "kind": item.kind,
        "label": item.label,
        "product_candidate_id": item.product_candidate_id,
        "scene_revision_id": item.scene_revision_id,
        "amount": float(item.amount) if item.amount is not None else None,
        "currency": item.currency,
        "quantity": item.quantity,
        "effective_amount": effective_amount,
        "effective_currency": effective_currency,
        "effective_quantity": effective_quantity,
        "contribution": contribution,
        "takeoff": takeoff,
        "unknowns": sorted(set(unknowns)),
        "incomplete": bool(unknowns) or contribution is None,
    }


async def budget_report(
    session: AsyncSession, project_id: str, variant_id: str
) -> dict[str, Any]:
    """Report bound to the variant's HEAD scene revision.

    Totals are computed over priced (complete) items only; incomplete items
    surface as ``unknowns`` (never zero-coerced into the totals). The delta
    consumer (:func:`services.variants.compare_variants`) treats any
    incomplete report as incomparable.
    """
    variant = await session.get(SceneVariantRow, variant_id)
    if variant is None or variant.project_id != project_id:
        raise BudgetError("variant not found", status_code=404, code="variant_not_found")
    revision = await session.get(SceneRevisionRow, variant.head_scene_revision_id)
    scene = (
        Scene.model_validate(revision.scene_json)
        if revision is not None
        else Scene(scene_id="scene.empty", project_id=project_id)
    )
    items = await list_budget_items(session, project_id, variant_id)

    candidate_ids = {item.product_candidate_id for item in items if item.product_candidate_id}
    candidates: dict[str, ProductCandidateRow | None] = {}
    for candidate_id in candidate_ids:
        row = await session.get(ProductCandidateRow, candidate_id)
        if row is not None and row.project_id == project_id:
            candidates[candidate_id] = row
        else:
            candidates[candidate_id] = None

    priced = [_price_item(item, scene, candidates) for item in items]
    known_total = 0.0
    contingency_total = 0.0
    currencies: set[str] = set()
    for view, item in zip(priced, items, strict=True):
        if view["contribution"] is None:
            continue
        if view["effective_currency"]:
            currencies.add(view["effective_currency"])
        # Contingency = an explicit manual item flagged in metadata.
        is_contingency = item.kind == "manual" and bool(
            (item.metadata_json or {}).get("contingency")
        )
        if is_contingency:
            contingency_total += view["contribution"]
        else:
            known_total += view["contribution"]

    mixed_currency = len(currencies) > 1
    report_unknowns = sorted(
        {unknown for view in priced for unknown in view["unknowns"]}
        | ({"mixed_currency"} if mixed_currency else set())
    )
    totals = {
        "known": round(known_total, 2),
        "contingency": round(contingency_total, 2),
        "grand_total": round(known_total + contingency_total, 2),
    }
    return {
        "variant_id": variant_id,
        "scene_revision_id": variant.head_scene_revision_id,
        "totals": totals,
        "currency": next(iter(sorted(currencies))) if len(currencies) == 1 else None,
        "currencies": sorted(currencies),
        "incomplete": bool(report_unknowns),
        "unknowns": report_unknowns,
        "items": priced,
    }
