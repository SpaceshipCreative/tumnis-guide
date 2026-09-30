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
    live,
    mcp_server,
    metrics,
    modules,
    ops_status,
    security_headers,
    settings_router,
    telemetry,
    testing_routes,
)
from tumnis.core.bodylimit import BodyLimitMiddleware
from tumnis.core.clock import Clock, OverridableClock, SystemClock
from tumnis.core.errors import document_problem_media_type, install_problem_handlers
from tumnis.core.etag import ETagMiddleware
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
IMMUTABLE = "public, max-age=31536000, immutable"


class ShellFiles(StaticFiles):
    """The built frontend. Anything but GET/HEAD is 404, not 405, so unknown API routes
    (for example the test routes with real adapters) are not found rather than refused.
    An unknown page path (`/login`, `/setup`, P0-13) gets the shell's index.html, so the
    app's own router takes it."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        if scope["method"] not in ("GET", "HEAD"):
            raise HTTPException(status_code=404)
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            first, last = path.partition("/")[0], path.rsplit("/", 1)[-1]
            if exc.status_code != HTTPStatus.NOT_FOUND or first in NOT_SHELL or "." in last:
                raise
            response = await super().get_response("index.html", scope)
        # Hashed assets never change under their name; everything else (index.html, sw.js,
        # the manifest, icons) is revalidated so a deploy reaches the next open (P0-22).
        hashed = path.startswith("assets/")
        response.headers["Cache-Control"] = IMMUTABLE if hashed else "no-cache"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """The cache invalidation listener (P0-08) and the live hub (P0-22) run beside the
    server; they reconnect on their own, so a database that is down at start does not stop
    the api. The MCP session manager (P2-01) runs for the app's whole life."""
    settings: Settings = app.state.settings
    stop = asyncio.Event()
    listener = cache.CacheInvalidationListener(settings.database_direct_url)
    task = asyncio.create_task(listener.run(stop))
    hub_task = asyncio.create_task(app.state.live_hub.run(stop))  # /ws fan-out (P0-22)
    runner_task = asyncio.create_task(app.state.runner_hub.run(stop))  # /ws/runner (P1-04)
    try:
        async with contextlib.AsyncExitStack() as stack:
            # /mcp (P2-01): the SDK's session manager must run, or the first call fails.
            await stack.enter_async_context(app.state.mcp_session_manager.run())
            yield
    finally:
        stop.set()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.gather(task, hub_task, runner_task), cache.POLL_S * 5)
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


def module_routers(name: str = "router") -> list[APIRouter]:
    """Each module's `router` (tumnis.modules.<m>.router.router), when it declares one;
    `name="settings_router"` gives the modules' routers under /v1/settings instead."""
    found = []
    for module in modules.MODULES:
        router = getattr(importlib.import_module(f"tumnis.modules.{module}.router"), name, None)
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
    for router in module_routers("settings_router"):  # P1-10: /settings/working-hours
        if router is not auth_router.settings_router:
            v1.include_router(router)
    v1.include_router(settings_router.router)  # P0-26: after the modules' own sections
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
    shell_dir: Path | None = None,
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
    if settings.tumnis_adapters == "fake":
        clock = OverridableClock(clock)  # POST /v1/test/clock can fix it (issue #6)
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
    # ETags (P0-22) are innermost; authentication (P0-13: the principal from the session
    # cookie) next, inside the correlation ID; the security headers (P0-16) sit outside
    # all of it (installed above). Rate limits, the Origin check and CSRF run in
    # TumnisRoute, where the route's policy is known.
    app.add_middleware(ETagMiddleware)  # innermost: tags the body the route built (P0-22)
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
    app.state.live_hub = live.LiveHub(settings.database_direct_url)
    # WS /ws (P0-22). Added on the app itself: FastAPI's walker loses the path of a
    # WebSocket route inside an included router, and the route sweeps read it.
    app.add_api_websocket_route("/ws", live.live_socket, name="live_socket")
    # WS /ws/runner (P1-04): runner daemons dial in with a device token. Imported by name,
    # as module_routers does, so no module's tests import agents through this root.
    agents_ws = importlib.import_module("tumnis.modules.agents.ws")
    app.state.runner_hub = agents_ws.RunnerHub(
        settings.database_direct_url, settings.dbos_system_url
    )
    app.add_api_websocket_route("/ws/runner", agents_ws.runner_socket, name="runner_socket")
    # /mcp (P2-01): every module's ops as tools; a raw route, so POST /mcp is not redirected.
    wiring.load_mcp()
    app.state.mcp_session_manager = mcp_server.session_manager()
    app.router.routes.append(mcp_server.mcp_route())
    shell = shell_dir or SHELL_DIR
    if shell.is_dir():
        app.mount("/", ShellFiles(directory=shell, html=True), name="shell")
    return app
