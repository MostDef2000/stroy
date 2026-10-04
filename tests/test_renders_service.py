from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from stroy.db.base import Base
from stroy.db.models import ProjectRow, RenderManifestRow
from stroy.services.renders import list_render_manifests


async def test_list_render_manifests_orders_newest_first(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'renders.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    older = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    newer = older + timedelta(hours=1)

    async with session_factory() as session:
        session.add(ProjectRow(id="project.1", name="Render project"))
        # Rows are inserted oldest-first, so only an explicit created_at DESC
        # ordering (not insertion/rowid order) yields newest-first output.
        session.add(
            RenderManifestRow(
                id="render.older",
                project_id="project.1",
                job_id="job.older",
                scene_revision_id="revision.older",
                camera_id="camera.main",
                manifest_json={},
                created_at=older,
            )
        )
        session.add(
            RenderManifestRow(
                id="render.newer",
                project_id="project.1",
                job_id="job.newer",
                scene_revision_id="revision.newer",
                camera_id="camera.main",
                manifest_json={},
                created_at=newer,
            )
        )
        await session.commit()

    async with session_factory() as session:
        rows = await list_render_manifests(session, "project.1")

    assert [row.id for row in rows] == ["render.newer", "render.older"]

    await engine.dispose()
