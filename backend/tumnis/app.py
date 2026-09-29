"""FastAPI application factory: routers, MCP mount, middleware, lifespan.

`create_app` does no I/O: it must start while Postgres is down so liveness can answer (the
boot checks run in the CLI before uvicorn starts, not in the ASGI lifespan).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from tumnis import wiring
from tumnis.core import db, health, ops_status, testing_routes
from tumnis.core.clock import Clock, SystemClock
from tumnis.core.request_meta import RequestMetaMiddleware
from tumnis.settings import Settings

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
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    await db.dispose()


def create_app(settings: Settings | None = None, clock: Clock | None = None) -> FastAPI:
    settings = settings or Settings()  # values come from the environment
    db.configure(settings.database_url, settings.database_direct_url)

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
    # Correlation ID, source address and user agent for the audit log (P0-15).
    app.add_middleware(RequestMetaMiddleware)
    app.state.settings = settings
    app.state.clock = clock or SystemClock()
    app.include_router(health.router)
    if settings.tumnis_adapters == "fake":
        app.include_router(testing_routes.router)
    if SHELL_DIR.is_dir():
        app.mount("/", ShellFiles(directory=SHELL_DIR, html=True), name="shell")
    return app
