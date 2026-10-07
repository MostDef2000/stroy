"""Scene-variant service tests (R4): branching, lifecycle, lineage, compare.

Service-level tests over a scratch sqlite DB (no HTTP layer): revisions and
variants are seeded as rows; commands run through the real
``services.variants.apply_variant_command`` / ``apply_command`` engine.

Core invariants under test:
- post-0015 schema allows sibling revisions (two children, one parent);
- exactly one ``approved`` variant per project (auto-demotion);
- fork/restore targets must lie in the variant's lineage (409) and appends
  are guarded by the expected head (``CommandConflict``);
- variant deletion never touches revisions;
- compare output is deterministic.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from stroy.db.base import Base
from stroy.db.models import (
    BudgetItemRow,
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
from stroy.domain.commands import CommandConflict
from stroy.services.budget import add_budget_item
from stroy.services.variants import (
    VariantError,
    append_variant_revision,
    apply_variant_command,
    approve_variant,
    compare_variants,
    create_variant_from_current,
    delete_variant,
    fork_variant,
    get_variant,
    list_variants,
    patch_variant,
    restore_variant_revision,
)


@pytest.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'variants.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def _scene(project_id: str = "p1") -> Scene:
    return Scene(
        scene_id="scene.main",
        project_id=project_id,
        entities=[
            SceneEntity(
                id="room.living",
                kind=EntityKind.ROOM,
                geometry={"wall_ids": ["wall.n"]},
            ),
            SceneEntity(
                id="wall.n",
                kind=EntityKind.WALL,
                room_id="room.living",
                geometry={
                    "a": [0, 0],
                    "b": [4000, 0],
                    "dimensions_mm": {"length": 4000, "thickness": 100, "height": 2700},
                },
            ),
        ],
    )


async def _seed(factory, project_id: str = "p1") -> SceneRevisionRow:
    """One project with a single canonical revision (the fork point)."""
    scene = _scene(project_id)
    async with factory() as session:
        session.add(ProjectRow(id=project_id, name="Flat"))
        revision = SceneRevisionRow(
            id="rev.root",
            project_id=project_id,
            content_hash="seed",
            scene_json=scene.model_dump(mode="json", exclude_none=True),
        )
        session.add(revision)
        await session.commit()
    return revision


def _set_color(base_revision_id: str, color: str, target_id: str = "room.living") -> DesignCommand:
    return DesignCommand(
        command_id=f"cmd.{uuid4()}",
        base_revision_id=base_revision_id,
        operation=CommandOperation.SET_COLOR,
        target_id=target_id,
        parameters={"color": color},
        origin=CommandOrigin.USER,
    )


async def _variant(factory, project_id: str, variant_id: str) -> SceneVariantRow:
    async with factory() as session:
        return await session.get(SceneVariantRow, variant_id)


async def _head_id(factory, variant_id: str) -> str:
    row = await _variant(factory, "p1", variant_id)
    assert row is not None
    return row.head_scene_revision_id


# ---------------------------------------------------------------------------
# branching


async def test_sibling_branching_revisions_coexist(db) -> None:
    await _seed(db)
    async with db() as session:
        first = await create_variant_from_current(session, "p1", "Wall paint")
        second = await create_variant_from_current(session, "p1", "Floor swap")
        assert first.head_scene_revision_id == "rev.root"
        assert second.head_scene_revision_id == "rev.root"

        # Each variant appends its own child under the SAME parent revision —
        # impossible before 0015, the expected-head guard is service-side now.
        r1 = await apply_variant_command(
            session, "p1", first.id, _set_color("rev.root", "red"),
            expected_head_revision_id="rev.root",
        )
        r2 = await apply_variant_command(
            session, "p1", second.id, _set_color("rev.root", "blue"),
            expected_head_revision_id="rev.root",
        )

    assert r1.parent_revision_id == r2.parent_revision_id == "rev.root"
    assert r1.id != r2.id
    assert (await _variant(db, "p1", first.id)).head_scene_revision_id == r1.id
    assert (await _variant(db, "p1", second.id)).head_scene_revision_id == r2.id
    # And the canonical chain still starts at the untouched root.
    async with db() as session:
        roots = (
            await session.execute(
                select(SceneRevisionRow).where(
                    SceneRevisionRow.parent_revision_id.is_(None),
                    SceneRevisionRow.project_id == "p1",
                )
            )
        ).scalars().all()
        assert [row.id for row in roots] == ["rev.root"]


async def test_stale_expected_head_raises_conflict(db) -> None:
    await _seed(db)
    async with db() as session:
        variant = await create_variant_from_current(session, "p1", "V")
        head = await apply_variant_command(
            session, "p1", variant.id, _set_color("rev.root", "red"),
            expected_head_revision_id="rev.root",
        )
        # Caller still holds the OLD head as its optimistic lock.
        with pytest.raises(CommandConflict, match="stale base revision"):
            await apply_variant_command(
                session, "p1", variant.id, _set_color(head.id, "blue"),
                expected_head_revision_id="rev.root",
            )
        # Direct append with a stale expected head is equally refused.
        with pytest.raises(CommandConflict, match="stale base revision"):
            await append_variant_revision(
                session, "p1", variant.id, "rev.root", _scene()
            )
        # The failed attempts left no revisions behind.
        count = (
            await session.execute(
                select(func.count()).select_from(SceneRevisionRow).where(
                    SceneRevisionRow.project_id == "p1"
                )
            )
        ).scalar_one()
        assert count == 2  # root + first command revision


# ---------------------------------------------------------------------------
# lifecycle


async def test_status_transitions_and_auto_demotion(db) -> None:
    await _seed(db)
    async with db() as session:
        first = await create_variant_from_current(session, "p1", "First")
        second = await create_variant_from_current(session, "p1", "Second")

        # draft → approved skips the shortlist: refused.
        with pytest.raises(VariantError, match="invalid status transition"):
            await patch_variant(session, "p1", first.id, {"status": "approved"})
        # Only shortlisted variants can be approved directly.
        with pytest.raises(VariantError, match="only shortlisted"):
            await approve_variant(session, "p1", first.id)

        await patch_variant(session, "p1", first.id, {"status": "shortlisted"})
        approved = await approve_variant(session, "p1", first.id)
        assert approved.status == "approved"

        # Second approved variant demotes the first, same transaction.
        await patch_variant(session, "p1", second.id, {"status": "shortlisted"})
        await approve_variant(session, "p1", second.id)
        assert (await _variant(db, "p1", first.id)).status == "shortlisted"
        assert (await _variant(db, "p1", second.id)).status == "approved"

        # Same-status patch is a no-op; archived is terminal.
        assert (await patch_variant(session, "p1", second.id, {"status": "approved"})).status == "approved"
        await patch_variant(session, "p1", second.id, {"status": "archived"})
        with pytest.raises(VariantError, match="invalid status transition"):
            await patch_variant(session, "p1", second.id, {"status": "draft"})
        with pytest.raises(VariantError, match="unknown variant status"):
            await patch_variant(session, "p1", first.id, {"status": "published"})


async def test_list_variants_filters_archived(db) -> None:
    await _seed(db)
    async with db() as session:
        archived = await create_variant_from_current(session, "p1", "Old")
        live = await create_variant_from_current(session, "p1", "Live")
        await patch_variant(session, "p1", archived.id, {"status": "archived"})

        visible = await list_variants(session, "p1")
        assert [row.id for row in visible] == [live.id]
        everything = await list_variants(session, "p1", include_archived=True)
        assert {row.id for row in everything} == {live.id, archived.id}
        only_archived = await list_variants(session, "p1", status="archived")
        assert [row.id for row in only_archived] == [archived.id]


# ---------------------------------------------------------------------------
# fork + restore lineage


async def test_fork_picks_lineage_point_and_defaults_title(db) -> None:
    await _seed(db)
    async with db() as session:
        source = await create_variant_from_current(session, "p1", "Source")
        head = await apply_variant_command(
            session, "p1", source.id, _set_color("rev.root", "red"),
            expected_head_revision_id="rev.root",
        )

        # Default fork: spans the source's base→head, title suffixed.
        tip_fork = await fork_variant(session, "p1", source.id)
        assert tip_fork.title == "Source (fork)"
        assert tip_fork.base_scene_revision_id == "rev.root"
        assert tip_fork.head_scene_revision_id == head.id

        # Explicit fork point inside the lineage: back at the root.
        root_fork = await fork_variant(
            session, "p1", source.id, title="From root", from_revision_id="rev.root"
        )
        assert root_fork.base_scene_revision_id == "rev.root"

        # A revision outside the lineage (unknown id) → 409.
        with pytest.raises(VariantError, match="revision_not_in_lineage|not in the lineage"):
            await fork_variant(
                session, "p1", source.id, from_revision_id="rev.nowhere"
            )
        assert await fork_variant(session, "p1", "v.missing") is None


async def test_restore_requires_lineage_and_current_head(db) -> None:
    await _seed(db)
    async with db() as session:
        variant = await create_variant_from_current(session, "p1", "V")
        first = await apply_variant_command(
            session, "p1", variant.id, _set_color("rev.root", "red"),
            expected_head_revision_id="rev.root",
        )
        second = await apply_variant_command(
            session, "p1", variant.id, _set_color(first.id, "blue"),
            expected_head_revision_id=first.id,
        )

        # Restore the ROOT scene: new revision on top of the head, no color.
        restored = await restore_variant_revision(
            session, "p1", variant.id,
            target_revision_id="rev.root", expected_head_revision_id=second.id,
        )
        assert restored.parent_revision_id == second.id
        scene = Scene.model_validate(restored.scene_json)
        assert all("color" not in e.metadata for e in scene.entities)

        # A revision of the same project OUTSIDE the lineage → 409 code.
        stray = SceneRevisionRow(
            id="rev.stray", project_id="p1", parent_revision_id=None,
            content_hash="stray", scene_json=_scene().model_dump(mode="json", exclude_none=True),
        )
        session.add(stray)
        await session.commit()
        with pytest.raises(VariantError) as excinfo:
            await restore_variant_revision(
                session, "p1", variant.id,
                target_revision_id="rev.stray", expected_head_revision_id=restored.id,
            )
        assert excinfo.value.status_code == 409
        assert excinfo.value.code == "revision_not_in_lineage"

        # Stale expected head → conflict, not lineage error.
        with pytest.raises(CommandConflict, match="stale base revision"):
            await restore_variant_revision(
                session, "p1", variant.id,
                target_revision_id="rev.root", expected_head_revision_id=first.id,
            )


# ---------------------------------------------------------------------------
# deletion


async def test_delete_variant_keeps_revisions_and_budget_rows(db) -> None:
    await _seed(db)
    async with db() as session:
        variant = await create_variant_from_current(session, "p1", "Doomed")
        await apply_variant_command(
            session, "p1", variant.id, _set_color("rev.root", "red"),
            expected_head_revision_id="rev.root",
        )
        await add_budget_item(
            session, "p1", variant.id,
            {"kind": "manual", "label": "Демонтаж", "amount": 5000, "currency": "RUB"},
        )

        revisions_before = (
            await session.execute(
                select(func.count()).select_from(SceneRevisionRow).where(
                    SceneRevisionRow.project_id == "p1"
                )
            )
        ).scalar_one()

        assert await delete_variant(session, "p1", variant.id) is True
        assert await delete_variant(session, "p1", variant.id) is False
        assert await get_variant(session, "p1", variant.id) is None

        revisions_after = (
            await session.execute(
                select(func.count()).select_from(SceneRevisionRow).where(
                    SceneRevisionRow.project_id == "p1"
                )
            )
        ).scalar_one()
        assert revisions_after == revisions_before  # revisions never touched
        budget_rows = (
            await session.execute(select(func.count()).select_from(BudgetItemRow))
        ).scalar_one()
        assert budget_rows == 1  # orphaned by design, cleaned by project cascade


# ---------------------------------------------------------------------------
# compare


async def test_compare_variants_is_deterministic(db) -> None:
    await _seed(db)
    async with db() as session:
        left = await create_variant_from_current(session, "p1", "Red")
        right = await create_variant_from_current(session, "p1", "Blue")
        await apply_variant_command(
            session, "p1", left.id, _set_color("rev.root", "red"),
            expected_head_revision_id="rev.root",
        )
        await apply_variant_command(
            session, "p1", right.id, _set_color("rev.root", "blue"),
            expected_head_revision_id="rev.root",
        )

        first = await compare_variants(session, "p1", left.id, right.id)
        second = await compare_variants(session, "p1", left.id, right.id)
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    assert first["left"]["variant_id"] == left.id
    assert first["right"]["variant_id"] == right.id
    assert first["entities"]["added"] == []
    assert first["entities"]["removed"] == []
    assert first["entities"]["modified"] == [
        {"id": "room.living", "changes": ["metadata.color"]}
    ]
    assert first["materials"] == {"added": [], "removed": []}
    assert first["validation"] == {"added": [], "resolved": []}
    # Both budgets empty and complete → zero delta is computable.
    assert first["budget"]["delta"] == {
        "contingency": 0.0, "grand_total": 0.0, "known": 0.0
    }
    assert first["renders"] == {"left": [], "right": []}

    # Unknown variant → 404 mapping.
    async with db() as session:
        with pytest.raises(VariantError) as excinfo:
            await compare_variants(session, "p1", left.id, "v.missing")
        assert excinfo.value.status_code == 404


async def test_compare_budget_delta_null_when_incomplete(db) -> None:
    await _seed(db)
    async with db() as session:
        left = await create_variant_from_current(session, "p1", "With gap")
        right = await create_variant_from_current(session, "p1", "Empty")
        await add_budget_item(
            session, "p1", left.id,
            {"kind": "manual", "label": "Без цены"},  # no amount → missing_price
        )
        report = await compare_variants(session, "p1", left.id, right.id)
        assert report["budget"]["left"]["incomplete"] is True
        assert report["budget"]["delta"] is None
