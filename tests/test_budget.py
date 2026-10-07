"""Variant budget service tests (R4): takeoff math, totals, unknowns.

The geometry-driven expectations are hand-computed for the fixture scene
(5000×4000 room, door 900×2100, window 1200×1500):

- floor bbox 5000×4000 → 20 m²; waste 0.1 → 22 m²; package 2.5 → ceil(8.8) = 9
  packages × 890 = 8010.00;
- baseboard run 18000 − 900 (door; window does not interrupt) = 17100 mm =
  17.1 linear m × 500 = 8550.00.

Locked behavior: only items with an EXPLICIT amount default their currency to
RUB — takeoff-priced items must carry ``currency`` in the payload, otherwise
they surface ``missing_currency`` (never silently priced).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from stroy.db.base import Base
from stroy.db.models import (
    BudgetItemRow,
    ProductCandidateRow,
    ProjectRow,
    SceneRevisionRow,
    SceneVariantRow,
)
from stroy.domain.models import (
    CommandOperation,
    CommandOrigin,
    DesignCommand,
    EntityKind,
    Scene,
    SceneEntity,
)
from stroy.services.budget import (
    BudgetError,
    add_budget_item,
    budget_item_view,
    budget_report,
    delete_budget_item,
    get_budget_item,
    list_budget_items,
    patch_budget_item,
)
from stroy.services.variants import append_variant_revision

WALL_IDS = ["wall.north", "wall.south", "wall.west", "wall.east"]


@pytest.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'budget.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def _scene() -> Scene:
    def wall(wall_id: str, a: list[float], b: list[float], length: float) -> SceneEntity:
        return SceneEntity(
            id=wall_id,
            kind=EntityKind.WALL,
            room_id="room.living",
            geometry={
                "a": a,
                "b": b,
                "dimensions_mm": {"length": length, "thickness": 100, "height": 2700},
            },
        )

    return Scene(
        scene_id="scene.main",
        project_id="p1",
        entities=[
            SceneEntity(
                id="room.living",
                kind=EntityKind.ROOM,
                geometry={"wall_ids": list(WALL_IDS)},
            ),
            wall("wall.north", [0, 4000], [5000, 4000], 5000),
            wall("wall.south", [0, 0], [5000, 0], 5000),
            wall("wall.west", [0, 0], [0, 4000], 4000),
            wall("wall.east", [5000, 0], [5000, 4000], 4000),
            SceneEntity(
                id="floor.main",
                kind=EntityKind.FLOOR,
                room_id="room.living",
                geometry={"bbox": [0, 0, 5000, 4000]},
            ),
            SceneEntity(
                id="door.main",
                kind=EntityKind.DOOR,
                room_id="room.living",
                geometry={"host_wall_id": "wall.south", "width_mm": 900, "height_mm": 2100},
            ),
            SceneEntity(
                id="window.main",
                kind=EntityKind.WINDOW,
                room_id="room.living",
                geometry={"host_wall_id": "wall.north", "width_mm": 1200, "height_mm": 1500},
            ),
        ],
    )


@pytest.fixture
async def variant(db) -> str:
    """Project p1 with one revision (the scene above) and variant v1."""
    async with db() as session:
        session.add(ProjectRow(id="p1", name="Flat"))
        session.add(
            SceneRevisionRow(
                id="rev.1",
                project_id="p1",
                content_hash="seed",
                scene_json=_scene().model_dump(mode="json", exclude_none=True),
            )
        )
        session.add(
            SceneVariantRow(
                id="v1",
                project_id="p1",
                title="V",
                base_scene_revision_id="rev.1",
                head_scene_revision_id="rev.1",
            )
        )
        await session.commit()
    return "v1"


FLOOR_LAMINATE = {
    "kind": "material",
    "label": "Ламинат",
    "currency": "RUB",
    "metadata": {
        "takeoff": {
            "coverage_unit": "m2",
            "target_id": "floor.main",
            "waste_factor": 0.1,
            "package_size": 2.5,
            "unit_price": 890,
        }
    },
}


# ---------------------------------------------------------------------------
# takeoff


async def test_floor_takeoff_waste_and_packages(db, variant) -> None:
    async with db() as session:
        row = await add_budget_item(session, "p1", variant, FLOOR_LAMINATE)
        report = await budget_report(session, "p1", variant)

    assert report["incomplete"] is False
    assert report["unknowns"] == []
    assert report["currency"] == "RUB"
    assert report["totals"] == {
        "known": 8010.0, "contingency": 0.0, "grand_total": 8010.0
    }
    (item,) = report["items"]
    takeoff = item["takeoff"]
    assert takeoff["base_quantity"] == 20.0
    assert takeoff["required_quantity"] == 22.0  # 20 × 1.1
    assert takeoff["package_count"] == 9.0  # ceil(22 / 2.5)
    assert takeoff["unit_price"] == 890.0
    assert takeoff["subtotal"] == 8010.0
    assert item["contribution"] == 8010.0
    assert item["effective_quantity"] == 22.0
    assert row.scene_revision_id == "rev.1"  # explicit head binding at creation


async def test_takeoff_without_currency_is_incomplete(db, variant) -> None:
    """LOCKED: takeoff items do NOT default currency — only amount items do."""
    payload = {**FLOOR_LAMINATE}
    payload.pop("currency")
    async with db() as session:
        await add_budget_item(session, "p1", variant, payload)
        report = await budget_report(session, "p1", variant)

    (item,) = report["items"]
    assert item["unknowns"] == ["missing_currency"]
    assert item["contribution"] is None
    assert report["incomplete"] is True
    assert report["totals"]["grand_total"] == 0.0


async def test_baseboard_takeoff_linear_meters(db, variant) -> None:
    async with db() as session:
        await add_budget_item(
            session, "p1", variant,
            {
                "kind": "material",
                "label": "Плинтус",
                "currency": "RUB",
                "metadata": {
                    "takeoff": {
                        "coverage_unit": "linear_m",
                        "target_id": "room.living",
                        "unit_price": 500,
                    }
                },
            },
        )
        report = await budget_report(session, "p1", variant)

    (item,) = report["items"]
    takeoff = item["takeoff"]
    assert takeoff["base_quantity"] == 17.1  # 18000 mm − 900 door, window skipped
    assert takeoff["package_size"] is None
    assert takeoff["package_count"] == 17.1  # no packaging: raw required quantity
    assert item["contribution"] == 8550.0


async def test_each_takeoff_uses_explicit_quantity(db, variant) -> None:
    async with db() as session:
        await add_budget_item(
            session, "p1", variant,
            {
                "kind": "material",
                "label": "Мешки клея",
                "quantity": 4,
                "currency": "RUB",
                "metadata": {
                    "takeoff": {"coverage_unit": "each", "unit_price": 300}
                },
            },
        )
        report = await budget_report(session, "p1", variant)

    (item,) = report["items"]
    assert item["takeoff"]["base_quantity"] == 4.0
    assert item["takeoff"]["subtotal"] == 1200.0
    assert report["totals"]["grand_total"] == 1200.0


async def test_explicit_amount_overrides_takeoff(db, variant) -> None:
    async with db() as session:
        await add_budget_item(
            session, "p1", variant,
            {**FLOOR_LAMINATE, "amount": 5000},
        )
        report = await budget_report(session, "p1", variant)

    (item,) = report["items"]
    assert item["amount"] == 5000.0
    assert item["takeoff"] is None  # takeoff bypassed entirely
    assert item["effective_amount"] == 5000.0
    assert item["effective_quantity"] == 1.0  # default unit quantity
    assert report["totals"]["grand_total"] == 5000.0


async def test_missing_takeoff_inputs_surface_as_unknowns(db, variant) -> None:
    async with db() as session:
        # Unreachable target → quantity unknown.
        await add_budget_item(
            session, "p1", variant,
            {
                "kind": "material",
                "label": "Мимо",
                "currency": "RUB",
                "metadata": {
                    "takeoff": {"coverage_unit": "m2", "target_id": "nope", "unit_price": 1}
                },
            },
        )
        # No unit_price → priced quantity, unknown money.
        await add_budget_item(
            session, "p1", variant,
            {
                "kind": "material",
                "label": "Без цены",
                "currency": "RUB",
                "metadata": {"takeoff": {"coverage_unit": "m2", "target_id": "floor.main"}},
            },
        )
        report = await budget_report(session, "p1", variant)

    assert report["incomplete"] is True
    assert report["unknowns"] == ["missing_price", "missing_quantity"]
    assert all(item["contribution"] is None for item in report["items"])
    assert report["totals"]["grand_total"] == 0.0


async def test_material_items_validate_takeoff_shape(db, variant) -> None:
    async with db() as session:
        with pytest.raises(BudgetError) as no_takeoff:
            await add_budget_item(session, "p1", variant, {"kind": "material", "label": "X"})
        assert no_takeoff.value.code == "invalid_takeoff"

        with pytest.raises(BudgetError) as bad_unit:
            await add_budget_item(
                session, "p1", variant,
                {"kind": "material", "label": "X", "metadata": {"takeoff": {"coverage_unit": "m"}}},
            )
        assert bad_unit.value.code == "invalid_takeoff"

        with pytest.raises(BudgetError) as negative:
            await add_budget_item(
                session, "p1", variant,
                {
                    "kind": "material",
                    "label": "X",
                    "metadata": {
                        "takeoff": {"coverage_unit": "m2", "waste_factor": -1}
                    },
                },
            )
        assert negative.value.code == "invalid_takeoff"


# ---------------------------------------------------------------------------
# payload validation


async def test_payload_validation(db, variant) -> None:
    async with db() as session:
        cases = [
            ({"kind": "magic", "label": "X"}, "invalid_kind"),
            ({"kind": "candidate", "label": "X"}, "candidate_required"),
            ({"kind": "manual", "label": "  "}, "invalid_label"),
            ({"kind": "manual", "label": "X", "amount": -1}, "invalid_amount"),
            (
                {"kind": "manual", "label": "X", "quantity": -2},
                "invalid_quantity",
            ),
            (
                {"kind": "manual", "label": "X", "surprise": 1},
                "invalid_payload",
            ),
            (
                {"kind": "manual", "label": "X", "product_candidate_id": "c1"},
                "candidate_not_allowed",
            ),
        ]
        for payload, expected_code in cases:
            with pytest.raises(BudgetError) as excinfo:
                await add_budget_item(session, "p1", variant, payload)
            assert excinfo.value.code == expected_code, payload

        # Unknown variant → None (API maps to 404).
        assert await add_budget_item(session, "p1", "v.ghost", {"kind": "manual", "label": "X"}) is None


# ---------------------------------------------------------------------------
# totals: contingency, mixed currency, candidates


async def test_contingency_is_metadata_flagged_manual_item(db, variant) -> None:
    async with db() as session:
        await add_budget_item(
            session, "p1", variant,
            {"kind": "manual", "label": "Работы", "amount": 10000, "currency": "RUB"},
        )
        await add_budget_item(
            session, "p1", variant,
            {
                "kind": "manual",
                "label": "Резерв",
                "amount": 5000,
                "currency": "RUB",
                "metadata": {"contingency": True},
            },
        )
        report = await budget_report(session, "p1", variant)

    assert report["totals"] == {
        "known": 10000.0, "contingency": 5000.0, "grand_total": 15000.0
    }
    assert report["incomplete"] is False


async def test_mixed_currency_flags_incomplete(db, variant) -> None:
    async with db() as session:
        await add_budget_item(
            session, "p1", variant, {"kind": "manual", "label": "A", "amount": 100, "currency": "RUB"}
        )
        await add_budget_item(
            session, "p1", variant, {"kind": "manual", "label": "B", "amount": 5, "currency": "USD"}
        )
        report = await budget_report(session, "p1", variant)

    assert report["currencies"] == ["RUB", "USD"]
    assert report["currency"] is None  # no single currency
    assert report["unknowns"] == ["mixed_currency"]
    assert report["incomplete"] is True
    assert report["totals"]["known"] == 105.0  # contributions still listed
    assert report["totals"]["grand_total"] == 105.0


async def test_candidate_pricing_and_project_guard(db, variant) -> None:
    async with db() as session:
        session.add(
            ProductCandidateRow(
                id="c.1", project_id="p1", title="Диван", price=1299.90, currency="RUB"
            )
        )
        session.add(
            ProductCandidateRow(id="c.other", project_id="p2", title="Чужое", price=1.0)
        )
        await session.commit()

        row = await add_budget_item(
            session, "p1", variant,
            {"kind": "candidate", "label": "Диван", "product_candidate_id": "c.1"},
        )
        assert row.product_candidate_id == "c.1"
        # Candidate from ANOTHER project is refused.
        with pytest.raises(BudgetError) as foreign:
            await add_budget_item(
                session, "p1", variant,
                {"kind": "candidate", "label": "Чужое", "product_candidate_id": "c.other"},
            )
        assert foreign.value.code == "unknown_candidate"

        report = await budget_report(session, "p1", variant)
        (item,) = report["items"]
        assert item["effective_amount"] == 1299.90
        assert item["effective_currency"] == "RUB"
        assert item["contribution"] == 1299.90
        assert report["totals"]["grand_total"] == 1299.90


async def test_candidate_without_price_is_unknown(db, variant) -> None:
    async with db() as session:
        session.add(ProductCandidateRow(id="c.broke", project_id="p1", title="Без цены"))
        await session.commit()
        await add_budget_item(
            session, "p1", variant,
            {"kind": "candidate", "label": "Диван", "product_candidate_id": "c.broke"},
        )
        report = await budget_report(session, "p1", variant)
    (item,) = report["items"]
    assert item["unknowns"] == ["missing_price"]
    assert report["incomplete"] is True


# ---------------------------------------------------------------------------
# listing, patching, binding


async def test_list_budget_items_is_entry_order(db, variant) -> None:
    base = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    async with db() as session:
        # Inserted newest-first; only created_at ASC (not rowid) gives BoQ order.
        for offset, item_id in ((2, "third"), (1, "second"), (0, "first")):
            session.add(
                BudgetItemRow(
                    id=item_id,
                    project_id="p1",
                    variant_id=variant,
                    scene_revision_id="rev.1",
                    kind="manual",
                    label=item_id,
                    amount=1,
                    currency="RUB",
                    created_at=base + timedelta(minutes=offset),
                )
            )
        await session.commit()
        rows = await list_budget_items(session, "p1", variant)
    assert [row.id for row in rows] == ["first", "second", "third"]


async def test_patch_budget_item_rules(db, variant) -> None:
    async with db() as session:
        row = await add_budget_item(
            session, "p1", variant, {"kind": "manual", "label": "A", "amount": 100}
        )
        assert row.currency == "RUB"  # amount present → RUB default

        patched = await patch_budget_item(
            session, "p1", variant, row.id, {"label": "B", "amount": 250}
        )
        assert (patched.label, float(patched.amount)) == ("B", 250.0)

        # Kind is immutable; unknown fields refused.
        with pytest.raises(BudgetError) as immutable:
            await patch_budget_item(session, "p1", variant, row.id, {"kind": "material"})
        assert immutable.value.code == "kind_immutable"
        with pytest.raises(BudgetError) as unknown_field:
            await patch_budget_item(session, "p1", variant, row.id, {"nope": 1})
        assert unknown_field.value.code == "invalid_payload"

        # Amount + cleared currency → the RUB invariant is re-applied.
        cleared = await patch_budget_item(
            session, "p1", variant, row.id, {"currency": None}
        )
        assert cleared.currency == "RUB"

        assert await get_budget_item(session, "p1", "v.ghost", row.id) is None
        assert await delete_budget_item(session, "p1", variant, row.id) is True
        assert await delete_budget_item(session, "p1", variant, row.id) is False


async def test_items_bind_head_at_creation_not_latest(db, variant) -> None:
    """Items keep the revision they were created against; heads may move on."""
    async with db() as session:
        row = await add_budget_item(
            session, "p1", variant, {"kind": "manual", "label": "A", "amount": 100}
        )
        assert row.scene_revision_id == "rev.1"

        # Move the variant head (append a design revision with the same scene).
        scene = Scene.model_validate((await session.get(SceneRevisionRow, "rev.1")).scene_json)
        await append_variant_revision(
            session, "p1", variant, "rev.1", scene,
            command=DesignCommand(
                command_id="cmd.1",
                base_revision_id="rev.1",
                operation=CommandOperation.SET_COLOR,
                target_id="room.living",
                parameters={"color": "red"},
                origin=CommandOrigin.USER,
            ),
        )
        refreshed = await get_budget_item(session, "p1", variant, row.id)
        assert refreshed.scene_revision_id == "rev.1"  # binding never re-resolves
        assert budget_item_view(refreshed)["scene_revision_id"] == "rev.1"
