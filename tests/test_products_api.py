"""Products API tests (R3): import-url, manual CRUD, cascade, placement e2e.

Extractor and guard I/O are mocked at the app.state seams
(``product_url_extractor`` / ``product_image_fetcher``); the guard's failure
taxonomy is exercised by raising GuardError from the fake extractor.
"""

from __future__ import annotations

from io import BytesIO
from uuid import uuid4

from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
from PIL import Image
import pytest
from sqlalchemy import event, func, select

from stroy.api.app import create_app
from stroy.config import Settings
from stroy.db.models import AssetRow, ProductCandidateRow, ProjectRow
from stroy.domain.products import ProductCandidateFacts
from stroy.net.guard import GuardError
from stroy.services.assets import MemoryObjectStore


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        auto_create_schema=True,
        auth_username="owner",
        auth_password_hash=PasswordHasher().hash("secret"),
        session_cookie_secure=False,
        trusted_hosts="test",
        worker_token="worker-secret",
        storage_backend="memory",
    )


def png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (3, 2), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _enable_sqlite_foreign_keys(app) -> None:
    """Enforce FKs on every connection so a broken cascade order surfaces."""
    engine = app.state.session_factory.kw["bind"]

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


async def login(client: AsyncClient) -> str:
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": "secret"},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


async def _create_project(client: AsyncClient, headers: dict, name: str = "Flat") -> str:
    response = await client.post("/api/v1/projects", json={"name": name}, headers=headers)
    assert response.status_code == 201
    return response.json()["id"]


async def _init_scene(client: AsyncClient, headers: dict, project_id: str) -> str:
    response = await client.post(
        f"/api/v1/projects/{project_id}/scene",
        headers=headers,
        json={
            "scene_id": "scene.main",
            "project_id": project_id,
            "entities": [
                {"id": "room.living", "kind": "room", "locks": {}},
            ],
            "cameras": [],
        },
    )
    assert response.status_code == 201
    return response.json()["revision_id"]


class FakeExtractor:
    """UrlMetadataExtractor double: canned facts or a GuardError."""

    def __init__(
        self,
        facts: ProductCandidateFacts | None = None,
        error: GuardError | None = None,
    ) -> None:
        self.facts = facts
        self.error = error
        self.calls: list[str] = []

    async def extract(self, url: str) -> ProductCandidateFacts:
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        return self.facts or ProductCandidateFacts(extraction_confidence=0.0)


FULL_FACTS = ProductCandidateFacts(
    title="Диван Скандинавия",
    brand="HomeFlat",
    model="SK-100",
    price=1299.90,
    currency="RUB",
    width_mm=2200.0,
    depth_mm=950.0,
    height_mm=850.0,
    material="массив дуба, ткань",
    color="beige",
    preview_image_url=None,
    extraction_confidence=0.9,
)

TITLE_ONLY_FACTS = ProductCandidateFacts(
    title="Неизвестный предмет",
    extraction_confidence=0.3,
)


def fake_image_fetcher(*, status: int = 200, body: bytes = b"") -> object:
    """Image-fetch seam double: canned GuardedFetch-shaped page."""

    class _Page:
        def __init__(self) -> None:
            self.status = status
            self.content_type = "image/png" if body.startswith(b"\x89PNG") else None
            self.body = body

    async def fetch(url: str) -> _Page:
        return _Page()

    return fetch


# ---------------------------------------------------------------------------
# import-url
# ---------------------------------------------------------------------------


async def test_import_url_success_full_facts(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    extractor = FakeExtractor(facts=FULL_FACTS)
    app.state.product_url_extractor = extractor
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            response = await client.post(
                f"/api/v1/projects/{project_id}/products/import-url",
                headers=headers,
                json={"url": "https://shop.example.com/sofa-1"},
            )
            assert response.status_code == 201, response.text
            body = response.json()
            assert extractor.calls == ["https://shop.example.com/sofa-1"]

            candidate = body["candidate"]
            assert candidate["project_id"] == project_id
            assert candidate["source_url"] == "https://shop.example.com/sofa-1"
            assert candidate["title"] == "Диван Скандинавия"
            assert candidate["brand"] == "HomeFlat"
            assert candidate["model"] == "SK-100"
            assert candidate["price"] == 1299.90
            assert candidate["currency"] == "RUB"
            assert candidate["width_mm"] == 2200.0
            assert candidate["depth_mm"] == 950.0
            assert candidate["height_mm"] == 850.0
            # Descriptors are ARRAYS in the API contract.
            assert candidate["material_descriptors"] == ["массив дуба", "ткань"]
            assert candidate["color_descriptors"] == ["beige"]
            assert candidate["provenance"] == "extracted"
            assert candidate["extraction_confidence"] == 0.9

            assert body["missing_fields"] == []
            assert body["extraction"] == {"status": "ok", "confidence": 0.9}


async def test_import_url_partial_persists_with_missing_fields(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    app.state.product_url_extractor = FakeExtractor(
        facts=ProductCandidateFacts(title="Что-то", extraction_confidence=0.3)
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            response = await client.post(
                f"/api/v1/projects/{project_id}/products/import-url",
                headers=headers,
                json={"url": "https://shop.example.com/thing"},
            )
            assert response.status_code == 201
            body = response.json()
            assert body["candidate"]["title"] == "Что-то"
            assert body["candidate"]["provenance"] == "extracted"
            assert set(body["missing_fields"]) == {
                "brand",
                "model",
                "price",
                "currency",
                "width_mm",
                "depth_mm",
                "height_mm",
                "material",
                "color",
            }
            assert body["extraction"] == {"status": "ok", "confidence": 0.3}

            # A fully blank page still persists an (empty) candidate.
            app.state.product_url_extractor = FakeExtractor()  # empty facts
            response = await client.post(
                f"/api/v1/projects/{project_id}/products/import-url",
                headers=headers,
                json={"url": "https://shop.example.com/blank"},
            )
            assert response.status_code == 201
            body = response.json()
            assert body["candidate"]["title"] is None
            assert body["extraction"]["status"] == "empty"
            assert body["extraction"]["confidence"] == 0.0


@pytest.mark.parametrize(
    "reason",
    [
        "private_address",
        "redirect_private_address",
        "too_many_redirects",
        "invalid_scheme",
        "unsupported_content_type",
        "response_too_large",
    ],
)
async def test_import_url_guard_errors_are_422_not_500(settings, reason):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    app.state.product_url_extractor = FakeExtractor(error=GuardError(reason))
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            response = await client.post(
                f"/api/v1/projects/{project_id}/products/import-url",
                headers=headers,
                json={"url": "http://shop.example.com/x"},
            )
            assert response.status_code == 422
            assert response.json()["detail"] == {"code": "url_rejected", "reason": reason}

    # Nothing persisted for rejected URLs.
    async with app.state.session_factory() as db:
        remaining = await db.scalar(
            select(func.count()).select_from(ProductCandidateRow)
        )
    assert remaining == 0


async def test_import_url_timeout_reason(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    app.state.product_url_extractor = FakeExtractor(error=GuardError("timeout"))
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            response = await client.post(
                f"/api/v1/projects/{project_id}/products/import-url",
                headers=headers,
                json={"url": "http://shop.example.com/slow"},
            )
            assert response.status_code == 422
            assert response.json()["detail"]["reason"] == "timeout"


async def test_import_url_unknown_project_404(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    app.state.product_url_extractor = FakeExtractor(facts=FULL_FACTS)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            response = await client.post(
                f"/api/v1/projects/{uuid4()}/products/import-url",
                headers=headers,
                json={"url": "https://shop.example.com/x"},
            )
            assert response.status_code == 404


# ---------------------------------------------------------------------------
# preview asset download (internal, non-fatal on failure)
# ---------------------------------------------------------------------------


async def test_import_url_downloads_preview_into_asset_store(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    app.state.product_url_extractor = FakeExtractor(
        facts=FULL_FACTS.model_copy(
            update={"preview_image_url": "https://cdn.example.com/sofa.png"}
        )
    )
    app.state.product_image_fetcher = fake_image_fetcher(body=png_bytes())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            response = await client.post(
                f"/api/v1/projects/{project_id}/products/import-url",
                headers=headers,
                json={"url": "https://shop.example.com/sofa-1"},
            )
            assert response.status_code == 201, response.text
            preview_asset_id = response.json()["candidate"]["preview_asset_id"]
            assert preview_asset_id

            assets = (
                await client.get(f"/api/v1/projects/{project_id}/assets")
            ).json()
            by_id = {row["id"]: row for row in assets}
            assert by_id[preview_asset_id]["role"] == "product_preview"
            assert by_id[preview_asset_id]["provenance"] == "extracted"


async def test_import_url_preview_failure_is_non_fatal(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    app.state.product_url_extractor = FakeExtractor(
        facts=FULL_FACTS.model_copy(
            update={"preview_image_url": "https://cdn.example.com/sofa.png"}
        )
    )
    app.state.product_image_fetcher = fake_image_fetcher(status=404)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            response = await client.post(
                f"/api/v1/projects/{project_id}/products/import-url",
                headers=headers,
                json={"url": "https://shop.example.com/sofa-1"},
            )
            assert response.status_code == 201
            body = response.json()["candidate"]
            assert body["preview_asset_id"] is None
            assert body["metadata"]["preview_error"] == "http_404"
            assert body["title"] == "Диван Скандинавия"  # facts still persisted


# ---------------------------------------------------------------------------
# manual create
# ---------------------------------------------------------------------------


async def test_manual_create_from_source_asset(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "product_preview"},
                files={"file": ("sofa.png", png_bytes(), "image/png")},
            )
            assert uploaded.status_code == 201
            asset_id = uploaded.json()["id"]

            response = await client.post(
                f"/api/v1/projects/{project_id}/products",
                headers=headers,
                json={
                    "source_asset_id": asset_id,
                    "title": "Диван ручной ввод",
                    "brand": "HomeFlat",
                    "price": 50000.0,
                    "currency": "RUB",
                    "width_mm": 2100.0,
                    "depth_mm": 900.0,
                    "height_mm": 800.0,
                    "material_descriptors": ["массив дуба"],
                    "color_descriptors": ["графит", "беж"],
                },
            )
            assert response.status_code == 201, response.text
            candidate = response.json()
            assert candidate["provenance"] == "manual"
            assert candidate["source_asset_id"] == asset_id
            assert candidate["extraction_confidence"] is None
            assert candidate["material_descriptors"] == ["массив дуба"]
            assert candidate["color_descriptors"] == ["графит", "беж"]
            assert candidate["price"] == 50000.0


async def test_manual_create_rejects_foreign_or_unknown_asset(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_a = await _create_project(client, headers, "A")
            project_b = await _create_project(client, headers, "B")

            uploaded = await client.post(
                f"/api/v1/projects/{project_b}/assets",
                headers=headers,
                files={"file": ("x.png", png_bytes(), "image/png")},
            )
            foreign_asset_id = uploaded.json()["id"]

            foreign = await client.post(
                f"/api/v1/projects/{project_a}/products",
                headers=headers,
                json={"source_asset_id": foreign_asset_id, "title": "X"},
            )
            assert foreign.status_code == 422
            assert (
                foreign.json()["detail"]["code"] == "invalid_product_source_asset"
            )

            unknown = await client.post(
                f"/api/v1/projects/{project_a}/products",
                headers=headers,
                json={"source_asset_id": str(uuid4()), "title": "X"},
            )
            assert unknown.status_code == 422
            assert (
                unknown.json()["detail"]["code"] == "invalid_product_source_asset"
            )


# ---------------------------------------------------------------------------
# patch / list / get / delete
# ---------------------------------------------------------------------------


async def _import_candidate(client: AsyncClient, headers: dict, project_id: str, app):
    app.state.product_url_extractor = FakeExtractor(facts=FULL_FACTS)
    response = await client.post(
        f"/api/v1/projects/{project_id}/products/import-url",
        headers=headers,
        json={"url": "https://shop.example.com/sofa-1"},
    )
    assert response.status_code == 201
    return response.json()["candidate"]


async def test_patch_dims_moves_extracted_to_mixed(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            candidate = await _import_candidate(client, headers, project_id, app)
            original_updated = candidate["updated_at"]

            patched = await client.patch(
                f"/api/v1/projects/{project_id}/products/{candidate['id']}",
                headers=headers,
                json={"width_mm": 2050.0, "depth_mm": 920.0, "height_mm": 810.0},
            )
            assert patched.status_code == 200
            body = patched.json()
            assert body["provenance"] == "mixed"
            assert body["width_mm"] == 2050.0
            assert body["depth_mm"] == 920.0
            assert body["height_mm"] == 810.0
            assert body["updated_at"] >= original_updated
            # Untouched fields survive.
            assert body["brand"] == "HomeFlat"
            assert body["material_descriptors"] == ["массив дуба", "ткань"]


async def test_patch_descriptors_as_arrays_and_manual_stays_manual(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            manual = (
                await client.post(
                    f"/api/v1/projects/{project_id}/products",
                    headers=headers,
                    json={"title": "Ручной", "price": 100.0},
                )
            ).json()
            patched = await client.patch(
                f"/api/v1/projects/{project_id}/products/{manual['id']}",
                headers=headers,
                json={"material_descriptors": ["ротанг", "металл"]},
            )
            assert patched.status_code == 200
            body = patched.json()
            assert body["material_descriptors"] == ["ротанг", "металл"]
            # Manual candidates keep their provenance on edit.
            assert body["provenance"] == "manual"

            # Unknown candidate → 404.
            missing = await client.patch(
                f"/api/v1/projects/{project_id}/products/{uuid4()}",
                headers=headers,
                json={"title": "x"},
            )
            assert missing.status_code == 404


async def test_list_newest_first_with_filters(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            imported = await _import_candidate(client, headers, project_id, app)
            manual = (
                await client.post(
                    f"/api/v1/projects/{project_id}/products",
                    headers=headers,
                    json={"title": "Ручной", "price": 10.0},
                )
            ).json()

            listed = (await client.get(f"/api/v1/projects/{project_id}/products")).json()
            # newest first: the manual candidate was created after the import
            assert [row["id"] for row in listed] == [manual["id"], imported["id"]]

            by_manual = (
                await client.get(
                    f"/api/v1/projects/{project_id}/products",
                    params={"source": "manual"},
                )
            ).json()
            assert [row["id"] for row in by_manual] == [manual["id"]]

            by_url = (
                await client.get(
                    f"/api/v1/projects/{project_id}/products",
                    params={"source": "url"},
                )
            ).json()
            assert [row["id"] for row in by_url] == [imported["id"]]

            # Imported candidate has the full w/d/h triple.
            with_dims = (
                await client.get(
                    f"/api/v1/projects/{project_id}/products",
                    params={"has_dimensions": "true"},
                )
            ).json()
            assert [row["id"] for row in with_dims] == [imported["id"]]

            without_dims = (
                await client.get(
                    f"/api/v1/projects/{project_id}/products",
                    params={"has_dimensions": "false"},
                )
            ).json()
            assert [row["id"] for row in without_dims] == [manual["id"]]

            bad_filter = await client.get(
                f"/api/v1/projects/{project_id}/products", params={"source": "bogus"}
            )
            assert bad_filter.status_code == 422


async def test_get_unknown_and_delete_candidate_only(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            missing = await client.get(
                f"/api/v1/projects/{project_id}/products/{uuid4()}"
            )
            assert missing.status_code == 404

            # Candidate with an internally-created preview asset.
            app.state.product_url_extractor = FakeExtractor(
                facts=FULL_FACTS.model_copy(
                    update={"preview_image_url": "https://cdn.example.com/sofa.png"}
                )
            )
            app.state.product_image_fetcher = fake_image_fetcher(body=png_bytes())
            imported = (
                await client.post(
                    f"/api/v1/projects/{project_id}/products/import-url",
                    headers=headers,
                    json={"url": "https://shop.example.com/sofa-1"},
                )
            ).json()["candidate"]
            preview_asset_id = imported["preview_asset_id"]
            assert preview_asset_id

            deleted = await client.delete(
                f"/api/v1/projects/{project_id}/products/{imported['id']}",
                headers=headers,
            )
            assert deleted.status_code == 204

            again = await client.delete(
                f"/api/v1/projects/{project_id}/products/{imported['id']}",
                headers=headers,
            )
            assert again.status_code == 404

            # Deleting the candidate leaves assets untouched.
            assets = (
                await client.get(f"/api/v1/projects/{project_id}/assets")
            ).json()
            assert [row["id"] for row in assets] == [preview_asset_id]


async def test_delete_project_cascades_candidates_before_assets(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    _enable_sqlite_foreign_keys(app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers, "Doomed")

            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "product_preview"},
                files={"file": ("room.png", png_bytes(), "image/png")},
            )
            assert uploaded.status_code == 201
            asset_id = uploaded.json()["id"]

            # Candidate references the asset via a real FK (source_asset_id):
            # with PRAGMA foreign_keys=ON a wrong cascade order (assets before
            # candidates) would raise IntegrityError here.
            created = await client.post(
                f"/api/v1/projects/{project_id}/products",
                headers=headers,
                json={"source_asset_id": asset_id, "title": "Диван"},
            )
            assert created.status_code == 201

            deleted = await client.delete(
                f"/api/v1/projects/{project_id}", headers=headers
            )
            assert deleted.status_code == 204, deleted.text

            async with app.state.session_factory() as db:
                assert await db.get(ProjectRow, project_id) is None
                candidates_left = await db.scalar(
                    select(func.count())
                    .select_from(ProductCandidateRow)
                    .where(ProductCandidateRow.project_id == project_id)
                )
                assets_left = await db.scalar(
                    select(func.count())
                    .select_from(AssetRow)
                    .where(AssetRow.project_id == project_id)
                )
            assert candidates_left == 0
            assert assets_left == 0


async def test_products_auth_owner_and_csrf(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            # Reads require an owner session.
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as anonymous:
                unauthenticated = await anonymous.get(
                    f"/api/v1/projects/{project_id}/products"
                )
            assert unauthenticated.status_code == 401

            # Writes require the CSRF token.
            csrfless = await client.post(
                f"/api/v1/projects/{project_id}/products",
                json={"title": "no csrf"},
            )
            assert csrfless.status_code == 403

            ok = await client.post(
                f"/api/v1/projects/{project_id}/products",
                headers=headers,
                json={"title": "with csrf"},
            )
            assert ok.status_code == 201


# ---------------------------------------------------------------------------
# e2e: candidate → patch → placement via /scene/commands
# ---------------------------------------------------------------------------


async def test_candidate_to_scene_placement_e2e(settings):
    """Candidate facts flow into a placed design entity without any
    SceneEntity schema change: geometry carries the dimensions, state
    defaults to design, metadata links back to the candidate."""
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            revision_id = await _init_scene(client, headers, project_id)

            # 1. Import a candidate (partial: no dimensions).
            app.state.product_url_extractor = FakeExtractor(
                facts=ProductCandidateFacts(
                    title="Диван Скандинавия",
                    brand="HomeFlat",
                    extraction_confidence=0.7,
                )
            )
            imported = await client.post(
                f"/api/v1/projects/{project_id}/products/import-url",
                headers=headers,
                json={"url": "https://shop.example.com/sofa-1"},
            )
            assert imported.status_code == 201
            candidate = imported.json()["candidate"]
            assert imported.json()["missing_fields"]  # dims missing

            # 2. Owner fills the dimensions in → provenance becomes mixed.
            patched = await client.patch(
                f"/api/v1/projects/{project_id}/products/{candidate['id']}",
                headers=headers,
                json={"width_mm": 2200.0, "depth_mm": 950.0, "height_mm": 850.0},
            )
            assert patched.status_code == 200
            assert patched.json()["provenance"] == "mixed"

            # 3. Place the object into the design scene via the existing
            #    /scene/commands add_object (no schema change).
            placed = await client.post(
                f"/api/v1/projects/{project_id}/scene/commands",
                headers=headers,
                json={
                    "command_id": f"command-{uuid4()}",
                    "base_revision_id": revision_id,
                    "operation": "add_object",
                    "target_id": "object.sofa.imported",
                    "origin": "user",
                    "parameters": {
                        "entity": {
                            "id": "object.sofa.imported",
                            "kind": "furniture",
                            "display_name": "Диван Скандинавия",
                            "geometry": {
                                "dimensions_mm": [2200.0, 950.0, 850.0],
                            },
                            "metadata": {
                                "product_candidate_id": candidate["id"],
                            },
                        }
                    },
                },
            )
            assert placed.status_code == 200, placed.text

            # 4. The scene now carries the entity with dims, design state and
            #    the candidate link.
            scene_response = await client.get(
                f"/api/v1/projects/{project_id}/scene"
            )
            assert scene_response.status_code == 200
            entities = scene_response.json()["scene"]["entities"]
            sofa = next(
                (e for e in entities if e["id"] == "object.sofa.imported"), None
            )
            assert sofa is not None
            assert sofa["state"] == "design"
            assert sofa["geometry"] == {
                "dimensions_mm": [2200.0, 950.0, 850.0],
            }
            assert sofa["metadata"]["product_candidate_id"] == candidate["id"]
