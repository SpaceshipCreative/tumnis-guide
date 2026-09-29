"""FastAPI application factory: routers, MCP mount, middleware, lifespan.

`create_app` does no I/O: it must start while Postgres is down so liveness can answer (the
boot checks run in the CLI before uvicorn starts, not in the ASGI lifespan).
"""

import asyncio
import contextlib
import importlib
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from http import HTTPStatus
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from starlette.exceptions import HTTPException as StarletteHTTPException
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
    security_headers,
    telemetry,
    testing_routes,
)
from tumnis.core.bodylimit import BodyLimitMiddleware
from tumnis.core.clock import Clock, SystemClock
from tumnis.core.errors import document_problem_media_type, install_problem_handlers
from tumnis.core.principal import AuthenticationMiddleware
from tumnis.core.ratelimit import RateLimiter
from tumnis.core.request_meta import RequestMetaMiddleware
from tumnis.core.routing import new_request_log
from tumnis.migrate import release_revisions
from tumnis.modules.auth import router as auth_router
from tumnis.settings import Settings, install_master_keys, install_peppers, require_hosted_tls

# The built frontend (P0-22 replaces the placeholder shell); present in the image.
SHELL_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"


# Paths the single-page app never owns: an unknown one stays a 404, not the shell.
NOT_SHELL = ("v1", "health", "metrics", "mcp", "ws", "assets")


class ShellFiles(StaticFiles):
    """The built frontend. Anything but GET/HEAD is 404, not 405, so unknown API routes
    (for example the test routes with real adapters) are not found rather than refused.
    An unknown page path (`/login`, `/setup`, P0-13) gets the shell's index.html, so the
    app's own router takes it."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        if scope["method"] not in ("GET", "HEAD"):
            raise HTTPException(status_code=404)
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            first, last = path.partition("/")[0], path.rsplit("/", 1)[-1]
            if exc.status_code != HTTPStatus.NOT_FOUND or first in NOT_SHELL or "." in last:
                raise
            return await super().get_response("index.html", scope)


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


API_DESCRIPTION = """Tumnis Guide REST API. Every error is `application/problem+json` with a stable
`code`. Every write takes an `Idempotency-Key` header (8 to 255 of `A-Z a-z 0-9 _ - : .`):
a retry with the same key within 24 hours replays the stored response and adds the header
`Idempotent-Replayed: true`; the same key with a different request is 422
`idempotency_mismatch`. Writes name the `version` they read; a stale one is 409
`stale_version` with `current`. Lists page by `cursor` and `limit` and return
`next_cursor`. Limits: 429 `rate_limited` with `Retry-After`, 413 `body_too_large`."""


def operation_id(route: Any) -> str:
    """Stable operation IDs, `<first tag>_<function name>` (R-19): the generated client's
    names (`usage_get_usage` -> `usageGetUsage`) change only when a route is renamed."""
    tags = getattr(route, "tags", None)
    return f"{tags[0]}_{route.name}" if tags else str(route.name)


def module_routers() -> list[APIRouter]:
    """Each module's `router` (tumnis.modules.<m>.router.router), when it declares one."""
    found = []
    for module in modules.MODULES:
        router = getattr(importlib.import_module(f"tumnis.modules.{module}.router"), "router", None)
        if isinstance(router, APIRouter):
            found.append(router)
    return found


def v1_routes(settings: Settings, extra_routers: Sequence[APIRouter] = ()) -> APIRouter:
    """Everything under /v1: core's routers, every module's router and any extra ones
    (tests); the test routes only with fakes."""
    v1 = APIRouter(prefix="/v1")
    v1.include_router(deadletter.router)
    v1.include_router(audit_router.router)
    v1.include_router(auth_router.settings_router)  # R-14
    for router in module_routers():
        v1.include_router(router)
    if settings.tumnis_adapters == "fake":
        v1.include_router(testing_routes.router)
    for router in extra_routers:
        v1.include_router(router)
    return v1


def _document_problems(app: FastAPI) -> None:
    """The generated OpenAPI document, with problem answers as application/problem+json."""
    generate = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = document_problem_media_type(generate())
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]  # FastAPI's documented override


def create_app(
    settings: Settings | None = None,
    clock: Clock | None = None,
    extra_routers: Sequence[APIRouter] = (),
) -> FastAPI:
    settings = settings or Settings()  # values come from the environment
    metrics_token = settings.metrics_token()  # SettingsError: prod needs METRICS_TOKEN_FILE
    settings.check_database_tls()  # SettingsError: prod needs sslmode=verify-full (P0-16)
    master_keys = install_master_keys(settings)  # MasterKeyError on an unsafe key file
    require_hosted_tls(settings)  # SettingsError: hosted mode without an https base URL
    install_peppers(settings)  # session, CSRF and pre-auth tokens (P0-13)
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
    # Behind this release's migrations is down; ahead of them (a rollback) is ready (REL-4).
    health.register_health(
        "schema", health.schema_check(db.app_engine, release_revisions().position), critical=True
    )
    # Backups (P0-28): degraded, never down, while the last freshness check failed.
    health.register_health(
        "backups", ops_status.health_check(db.app_engine, "backups"), critical=False
    )
    wiring.register_module_health()
    wiring.register_adapter_health(health.register_health)

    app = FastAPI(
        title="Tumnis Guide",
        lifespan=lifespan,
        description=API_DESCRIPTION,
        openapi_url="/v1/openapi.json",
        generate_unique_id_function=operation_id,
        docs_url=None,
        redoc_url=None,
    )
    install_problem_handlers(app)
    _document_problems(app)
    telemetry.instrument_app(app)  # a SERVER span per request (P0-27)
    # Security headers wrap the whole stack, server errors included (P0-16, SEC-4); after
    # the instrumentation, which wraps the stack too, so the headers stay outermost.
    security_headers.install(app)
    # Middleware, innermost first (add_middleware wraps what is there): correlation ID,
    # source address and user agent for the audit log (P0-15); the body limit outside it
    # (P0-10); outermost the request histogram, timing everything below it (P0-27).
    # Authentication (P0-13, innermost: the principal from the session cookie) goes inside
    # the correlation ID; the security headers (P0-16) sit outside all of it (installed
    # above). Rate limits, the Origin check and CSRF run in TumnisRoute, where the route's
    # policy is known.
    app.add_middleware(AuthenticationMiddleware)
    app.add_middleware(RequestMetaMiddleware)
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(metrics.RequestMetricsMiddleware)
    app.state.settings = settings
    app.state.master_keys = master_keys
    app.state.metrics_token = metrics_token
    app.state.clock = clock
    app.state.rate_limiter = RateLimiter(clock)
    if settings.tumnis_adapters == "fake":
        app.state.request_log = new_request_log()  # GET /v1/test/requests (A0.2)
    app.include_router(health.router)
    app.include_router(metrics.router)  # GET /metrics, bearer (P0-27)
    app.include_router(v1_routes(settings, extra_routers))
    if SHELL_DIR.is_dir():
        app.mount("/", ShellFiles(directory=SHELL_DIR, html=True), name="shell")
    return app
