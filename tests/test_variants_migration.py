"""Migration 0015 tests: scene variants + budget items schema change.

Runs the real alembic chain against a scratch sqlite DB per test:

- upgrade 0014 → head drops the R1-era unnamed UNIQUE parent lock (replaced
  by a plain index) and creates the variant/budget tables;
- downgrade over an empty, linear graph restores the lock and the old shape;
- downgrade is REFUSED once forks (sibling revisions) or variant rows exist.

``migrations/env.py`` resolves the URL through ``get_settings()`` (lru_cached),
so the fixture pins ``STROY_DATABASE_URL`` and clears the settings cache
before AND after (an empty cache re-reads the restored env on next use).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from stroy.config import get_settings

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def scratch_db(tmp_path, monkeypatch):
    """Pin migrations to a per-test scratch sqlite DB."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'migr.db'}"
    monkeypatch.setenv("STROY_DATABASE_URL", url)
    get_settings.cache_clear()
    yield url
    get_settings.cache_clear()


def _alembic_config() -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    return cfg


def _sync_engine(url: str):
    return create_engine(url.replace("+aiosqlite", ""))


def _parent_unique_constraints(inspector) -> list[dict]:
    return [
        uc
        for uc in inspector.get_unique_constraints("scene_revisions")
        if list(uc.get("column_names") or []) == ["parent_revision_id"]
    ]


def _index_names(inspector, table: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table) if index["name"]}


def test_upgrade_drops_parent_unique_and_creates_variant_tables(scratch_db) -> None:
    cfg = _alembic_config()
    command.upgrade(cfg, "0014")
    command.upgrade(cfg, "head")

    inspector = inspect(_sync_engine(scratch_db))
    # The R1-era UNIQUE lock on the revision parent is gone...
    assert _parent_unique_constraints(inspector) == []
    # ...replaced by a plain index for parent-chain walks.
    assert "ix_scene_revisions_parent_revision_id" in _index_names(
        inspector, "scene_revisions"
    )

    tables = inspector.get_table_names()
    assert "scene_variants" in tables
    assert "budget_items" in tables
    assert {
        "ix_scene_variants_project_id",
        "ix_scene_variants_project_status",
        "ix_scene_variants_project_head",
        "ix_scene_variants_project_base",
    } <= _index_names(inspector, "scene_variants")
    assert {
        "ix_budget_items_project_id",
        "ix_budget_items_variant_id",
        "ix_budget_items_project_variant",
        "ix_budget_items_variant_revision",
    } <= _index_names(inspector, "budget_items")

    # Render/generation manifests carry the variant linkage.
    render_columns = {c["name"] for c in inspector.get_columns("render_manifests")}
    generation_columns = {
        c["name"] for c in inspector.get_columns("generation_manifests")
    }
    assert "variant_id" in render_columns
    assert "variant_id" in generation_columns
    assert "ix_render_manifests_variant_id" in _index_names(
        inspector, "render_manifests"
    )
    assert "ix_generation_manifests_variant_id" in _index_names(
        inspector, "generation_manifests"
    )


def test_downgrade_on_linear_empty_graph_restores_unique_lock(scratch_db) -> None:
    cfg = _alembic_config()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0014")

    inspector = inspect(_sync_engine(scratch_db))
    # The parent lock is back (unnamed, backend-generated, as before 0015).
    uniques = _parent_unique_constraints(inspector)
    assert len(uniques) == 1
    assert uniques[0].get("name") is None
    assert "ix_scene_revisions_parent_revision_id" not in _index_names(
        inspector, "scene_revisions"
    )
    tables = inspector.get_table_names()
    assert "scene_variants" not in tables
    assert "budget_items" not in tables
    render_columns = {c["name"] for c in inspector.get_columns("render_manifests")}
    generation_columns = {
        c["name"] for c in inspector.get_columns("generation_manifests")
    }
    assert "variant_id" not in render_columns
    assert "variant_id" not in generation_columns

    # Behavioral oracle: the restored lock rejects sibling revisions again.
    engine = _sync_engine(scratch_db)
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO projects (id, name, created_at) "
                "VALUES ('p1', 'Flat', :created_at)"
            ),
            {"created_at": now},
        )
        revision_values = {
            "created_at": now,
            "schema_version": "0.1.0",
            "content_hash": "h",
            "scene_json": "{}",
        }
        conn.execute(
            text(
                "INSERT INTO scene_revisions (id, project_id, parent_revision_id, "
                "schema_version, content_hash, scene_json, created_at) "
                "VALUES ('rev.root', 'p1', NULL, :schema_version, :content_hash, "
                ":scene_json, :created_at)"
            ),
            revision_values,
        )
        # First child under rev.root is legal...
        conn.execute(
            text(
                "INSERT INTO scene_revisions (id, project_id, "
                "parent_revision_id, schema_version, content_hash, "
                "scene_json, created_at) VALUES ('rev.a', 'p1', 'rev.root', "
                ":schema_version, :content_hash, :scene_json, :created_at)"
            ),
            revision_values,
        )
        # ...a SECOND child violates the restored linear-history lock.
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO scene_revisions (id, project_id, "
                    "parent_revision_id, schema_version, content_hash, "
                    "scene_json, created_at) VALUES ('rev.b', 'p1', 'rev.root', "
                    ":schema_version, :content_hash, :scene_json, :created_at)"
                ),
                revision_values,
            )
    engine.dispose()


def test_downgrade_refused_when_variant_rows_exist(scratch_db) -> None:
    cfg = _alembic_config()
    command.upgrade(cfg, "head")

    engine = _sync_engine(scratch_db)
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO projects (id, name, created_at) "
                "VALUES ('p1', 'Flat', :created_at)"
            ),
            {"created_at": now},
        )
        conn.execute(
            text(
                "INSERT INTO scene_variants (id, project_id, title, "
                "base_scene_revision_id, head_scene_revision_id, status, "
                "created_at, updated_at) VALUES ('v1', 'p1', 'V', 'rev', 'rev', "
                "'draft', :created_at, :created_at)"
            ),
            {"created_at": now},
        )
    with pytest.raises(RuntimeError, match="scene variant"):
        command.downgrade(cfg, "0014")
    engine.dispose()


def test_downgrade_refused_when_revision_graph_forks(scratch_db) -> None:
    cfg = _alembic_config()
    command.upgrade(cfg, "head")

    engine = _sync_engine(scratch_db)
    now = datetime.now(timezone.utc)
    revision_values = {
        "created_at": now,
        "schema_version": "0.1.0",
        "content_hash": "h",
        "scene_json": "{}",
    }
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO projects (id, name, created_at) "
                "VALUES ('p1', 'Flat', :created_at)"
            ),
            {"created_at": now},
        )
        conn.execute(
            text(
                "INSERT INTO scene_revisions (id, project_id, parent_revision_id, "
                "schema_version, content_hash, scene_json, created_at) "
                "VALUES ('rev.root', 'p1', NULL, :schema_version, :content_hash, "
                ":scene_json, :created_at)"
            ),
            revision_values,
        )
        # Two children under one parent: only possible post-0015.
        for child in ("rev.a", "rev.b"):
            conn.execute(
                text(
                    "INSERT INTO scene_revisions (id, project_id, "
                    "parent_revision_id, schema_version, content_hash, "
                    "scene_json, created_at) VALUES (:id, 'p1', 'rev.root', "
                    ":schema_version, :content_hash, :scene_json, :created_at)"
                ),
                {**revision_values, "id": child},
            )
    with pytest.raises(RuntimeError, match="branching history"):
        command.downgrade(cfg, "0014")
    engine.dispose()
