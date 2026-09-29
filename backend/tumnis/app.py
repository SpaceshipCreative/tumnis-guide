"""FastAPI application factory: routers, MCP mount, middleware, lifespan.

`create_app` does no I/O: it must start while Postgres is down so liveness can answer (the
boot checks run in the CLI before uvicorn starts, not in the ASGI lifespan).
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from tumnis import wiring
from tumnis.core import (
    audit_router,
    cache,
    db,
    deadletter,
    health,
    metrics,
    modules,
    ops_status,
    telemetry,
    testing_routes,
)
from tumnis.core.clock import Clock, SystemClock
from tumnis.core.errors import install_problem_handlers
from tumnis.core.request_meta import RequestMetaMiddleware
from tumnis.modules.auth import router as auth_router
from tumnis.modules.usage import router as usage_router
from tumnis.settings import Settings, install_master_keys

# The built frontend (P0-22 replaces the placeholder shell); present in the image.
SHELL_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class ShellFiles(StaticFiles):
    """The built frontend. Anything but GET/HEAD is 404, not 405, so unknown API routes
    (for example the test routes with real adapters) are not found rather than refused."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        if scope["method"] not in ("GET", "HEAD"):
            raise HTTPException(status_code=404)
        return await super().get_response(path, scope)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """The cache invalidation listener runs beside the server (P0-08); it reconnects on its
    own, so a database that is down at start does not stop the api."""
    settings: Settings = app.state.settings
    stop = asyncio.Event()
    listener = cache.CacheInvalidationListener(settings.database_direct_url)
    task = asyncio.create_task(listener.run(stop))
    try:
        yield
    finally:
        stop.set()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(task, cache.POLL_S * 5)
        deadletter.close()
        await metrics.dispose()
        await db.dispose()


def create_app(settings: Settings | None = None, clock: Clock | None = None) -> FastAPI:
    settings = settings or Settings()  # values come from the environment
    metrics_token = settings.metrics_token()  # SettingsError: prod needs METRICS_TOKEN_FILE
    master_keys = install_master_keys(settings)  # MasterKeyError on an unsafe key file
    db.configure(settings.database_url, settings.database_direct_url)
    modules.configure(settings)  # the deployment's module kill list
    clock = clock or SystemClock()
    cache.configure_backend(cache.InProcessCache(clock, publish=cache.pg_publisher(db.app_engine)))
    deadletter.configure(settings.dbos_system_url)  # the api enqueues through a DBOSClient
    metrics.configure(settings.dbos_system_url)  # queue depth and workflows at scrape time
    wiring.register_module_metrics()

    health.clear_health()
    health.register_health("postgres", health.sql_check(db.app_engine, "SELECT 1"), critical=True)
    health.register_health("dbos", health.dbos_check(settings.dbos_system_url), critical=True)
    # Backups (P0-28): degraded, never down, while the last freshness check failed.
    health.register_health(
        "backups", ops_status.health_check(db.app_engine, "backups"), critical=False
    )
    wiring.register_module_health()
    wiring.register_adapter_health(health.register_health)

    app = FastAPI(
        title="Tumnis Guide",
        lifespan=lifespan,
        openapi_url="/v1/openapi.json",
        docs_url=None,
        redoc_url=None,
    )
    install_problem_handlers(app)
    telemetry.instrument_app(app)  # a SERVER span per request (P0-27)
    # Correlation ID, source address and user agent for the audit log (P0-15).
    app.add_middleware(RequestMetaMiddleware)
    # Outermost: the request histogram times everything below it (P0-27).
    app.add_middleware(metrics.RequestMetricsMiddleware)
    app.state.settings = settings
    app.state.master_keys = master_keys
    app.state.metrics_token = metrics_token
    app.state.clock = clock
    app.include_router(health.router)
    app.include_router(metrics.router)  # GET /metrics, bearer (P0-27)
    app.include_router(deadletter.router)
    app.include_router(audit_router.router)
    app.include_router(auth_router.settings_router)  # R-14; P0-10 moves it onto v1_router
    app.include_router(usage_router.router)  # P0-21; P0-10 moves it onto v1_router
    if settings.tumnis_adapters == "fake":
        app.include_router(testing_routes.router)
    if SHELL_DIR.is_dir():
        app.mount("/", ShellFiles(directory=SHELL_DIR, html=True), name="shell")
    return app
