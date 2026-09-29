"""API key helpers for the P0-14 spec tests and the `key_client` fixture.

No assertions live here: spec-guard locks the test bodies, and these helpers adapt to the
routes and the auth api.

- `KeyClient`: an httpx client sending `Authorization: Bearer <key>` and an
  `Idempotency-Key` on every write that lacks one (no CSRF token: keys never need it).
- `key_client_for(app, ctx, scopes, projects=None, *, now)`: creates a key through
  `tumnis.modules.auth.api.create_key` in the context's workspace and returns a
  `KeyClient` on the app signed with it (`.key`, `.key_id`).
- `canary_router()`: synthetic `/v1/authz-canary/...` routes, one per way a route can name
  its project (`path:`, `query:`, `body:`, `lookup:`) plus scoped, key-only and
  session-only ones, so the authorization checks have something to fire on before the
  modules with scoped routes land (P0-17 onward). `CANARY_PROJECTS` maps the lookup
  route's row ids to their projects.
- `canary_app(settings, clock)`: `create_app` with the canary routes mounted.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Final

import httpx
from fastapi import Request
from pydantic import BaseModel

from tumnis.core.routing import RoutePolicy, route_policy, v1_router

if TYPE_CHECKING:
    from datetime import datetime

    from fastapi import APIRouter, FastAPI

    from tumnis.core.clock import Clock
    from tumnis.core.tenancy import WorkspaceContext
    from tumnis.settings import Settings

BASE_URL: Final = "https://test"
WRITE_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CANARY_PREFIX: Final = "/authz-canary"
CANARY_MODULE: Final = "authz_canary"
# lookup:<module> rows: row id -> project id (filled by the tests).
CANARY_PROJECTS: dict[uuid.UUID, uuid.UUID] = {}


class KeyClient(httpx.AsyncClient):
    """Sends the bearer key; adds an `Idempotency-Key` to writes that lack one."""

    key: str = ""
    key_id: uuid.UUID | None = None

    def __init__(self, key: str, **kwargs: Any) -> None:
        hooks = kwargs.pop("event_hooks", {}) or {}
        hooks.setdefault("request", []).append(self._add_write_headers)
        headers = {"Authorization": f"Bearer {key}", **kwargs.pop("headers", {})}
        super().__init__(event_hooks=hooks, headers=headers, **kwargs)
        self.key = key

    async def _add_write_headers(self, request: httpx.Request) -> None:
        if request.method in WRITE_METHODS and "Idempotency-Key" not in request.headers:
            request.headers["Idempotency-Key"] = f"test-{uuid.uuid4()}"


def bearer_client(app: FastAPI, key: str) -> KeyClient:
    """A client on the app that authenticates with `key` (any `tmn_`/`tmt_`/`tmd_` value)."""
    return KeyClient(key, transport=httpx.ASGITransport(app=app), base_url=BASE_URL)


async def key_client_for(
    app: FastAPI,
    ctx: WorkspaceContext,
    scopes: Iterable[str],
    projects: Iterable[uuid.UUID] | None = None,
    *,
    now: datetime,
    name: str = "test key",
) -> KeyClient:
    """A key made through the auth api (`create_key`) and a client that sends it."""
    from tumnis.modules.auth import api  # noqa: PLC0415

    body = api.KeyIn(
        name=name,
        scopes=sorted(scopes),
        project_ids=None if projects is None else sorted(projects, key=str),
    )
    created = await api.create_key(ctx, body, now=now)
    client = bearer_client(app, created.key)
    client.key_id = created.id
    return client


# --- The canary routes ----------------------------------------------------------------------


class CanaryBody(BaseModel):
    project_id: uuid.UUID
    note: str = "canary"


class CanaryOut(BaseModel):
    ok: bool = True
    principal: str


def _who(request: Request) -> CanaryOut:
    from tumnis.core.principal import principal_of  # noqa: PLC0415

    return CanaryOut(principal=principal_of(request).kind)


async def canary_project_of(ctx: WorkspaceContext, row_id: uuid.UUID) -> uuid.UUID | None:
    """The `lookup:authz_canary` resolver: the project of a canary row."""
    return CANARY_PROJECTS.get(row_id)


def canary_router() -> APIRouter:
    router = v1_router(CANARY_MODULE, prefix=CANARY_PREFIX, tags=["canary"])
    read = frozenset({"tasks:read"})
    write = frozenset({"tasks:write"})

    @router.get("/open")
    @route_policy(RoutePolicy(auth="session_or_key"))
    async def canary_open(request: Request) -> CanaryOut:
        return _who(request)

    @router.get("/scoped")
    @route_policy(RoutePolicy(auth="session_or_key", scopes=read))
    async def canary_scoped(request: Request) -> CanaryOut:
        return _who(request)

    @router.get("/key-only")
    @route_policy(RoutePolicy(auth="key_or_task_token", scopes=read))
    async def canary_key_only(request: Request) -> CanaryOut:
        return _who(request)

    @router.get("/session-only")
    @route_policy(RoutePolicy(auth="session"))
    async def canary_session_only(request: Request) -> CanaryOut:
        return _who(request)

    @router.get("/projects/{project_id}")
    @route_policy(RoutePolicy(auth="session_or_key", scopes=read, project_param="path:project_id"))
    async def canary_project_path(project_id: uuid.UUID, request: Request) -> CanaryOut:
        return _who(request)

    @router.post("/projects/{project_id}/write")
    @route_policy(
        RoutePolicy(
            auth="session_or_key",
            scopes=write,
            project_param="path:project_id",
            idempotent=False,
            not_idempotent_reason="test canary: records nothing",
        )
    )
    async def canary_project_write(project_id: uuid.UUID, request: Request) -> CanaryOut:
        return _who(request)

    @router.get("/by-query")
    @route_policy(RoutePolicy(auth="session_or_key", scopes=read, project_param="query:project_id"))
    async def canary_project_query(project_id: uuid.UUID, request: Request) -> CanaryOut:
        return _who(request)

    @router.post("/by-body")
    @route_policy(
        RoutePolicy(
            auth="session_or_key",
            scopes=write,
            project_param="body:project_id",
            idempotent=False,
            not_idempotent_reason="test canary: records nothing",
        )
    )
    async def canary_project_body(body: CanaryBody, request: Request) -> CanaryOut:
        return _who(request)

    @router.get("/rows/{row_id}")
    @route_policy(
        RoutePolicy(auth="session_or_key", scopes=read, project_param=f"lookup:{CANARY_MODULE}")
    )
    async def canary_project_lookup(row_id: uuid.UUID, request: Request) -> CanaryOut:
        return _who(request)

    return router


def register_canary_lookup() -> None:
    from tumnis.core.routing import register_project_lookup  # noqa: PLC0415

    register_project_lookup(CANARY_MODULE, canary_project_of)


def canary_app(settings: Settings, clock: Clock) -> FastAPI:
    """create_app with the canary routes (and their project lookup) mounted."""
    from tumnis.app import create_app  # noqa: PLC0415

    register_canary_lookup()
    return create_app(settings=settings, clock=clock, extra_routers=[canary_router()])


def canary_test_app(db: Any, dbos_sys_db: Any, clock: Clock, pepper_path: str) -> FastAPI:
    """The shared `app` fixture's app (per-test database, fakes, master key and pepper
    files, NullPool engines) with the canary routes mounted. Test modules override `app`
    with it, so `session_client` and `key_client` talk to the canary routes too."""
    from tests.fixtures import settings_for  # noqa: PLC0415
    from tumnis.core import db as core_db  # noqa: PLC0415

    built = canary_app(settings_for(db, dbos_sys_db, api_key_pepper_file=pepper_path), clock)
    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    return built
