from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from stroy.api.middleware import RequestContextMiddleware
from stroy.api.routes import router
from stroy.config import Settings, get_settings
from stroy.db import Base, create_engine_and_session_factory
from stroy.security import LoginThrottle
from stroy.services.assets import ObjectStore, create_object_store
from stroy.services.dispatch import JobDispatcher, create_job_dispatcher


def create_app(
    *,
    settings: Settings | None = None,
    object_store: ObjectStore | None = None,
    job_dispatcher: JobDispatcher | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    engine, session_factory = create_engine_and_session_factory(settings.database_url)
    store = object_store or create_object_store(settings)
    dispatcher = job_dispatcher or create_job_dispatcher(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.auto_create_schema:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        await store.initialize()
        await dispatcher.initialize()
        yield
        await dispatcher.close()
        await engine.dispose()

    app = FastAPI(title="STROY API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(RequestContextMiddleware)
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.object_store = store
    app.state.job_dispatcher = dispatcher
    app.state.login_throttle = LoginThrottle()

    hosts = [host.strip() for host in settings.trusted_hosts.split(",") if host.strip()]
    if hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    app.include_router(router)
    return app
