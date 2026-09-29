"""Route registry: every /v1 route is declared through `v1_router` with a `RoutePolicy`, writes
say whether they are idempotent, and lists page by cursor (P0-10, SAAS-1, REL-2, PERF-1).

A sweep over the real app's routes, nested included routers unpacked (FastAPI 0.141 keeps
included routers lazy), so a route a later work package adds is checked with no edit here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI

# Non-/v1 paths: operations endpoints and live channels only (A10).
OPS_PATHS = frozenset({"/health/live", "/health/ready", "/metrics", "/mcp", "/ws", "/ws/runner"})
DSN = "postgresql+psycopg://registry:registry@127.0.0.1:1/registry"


def _app() -> FastAPI:
    """The real app, built without I/O, with fakes (so the test routes are in it too)."""
    from tumnis.app import create_app  # noqa: PLC0415
    from tumnis.settings import Settings  # noqa: PLC0415

    settings = Settings(database_url=DSN, database_direct_url=DSN, tumnis_adapters="fake")
    return create_app(settings)


@pytest.mark.req("SAAS-1", "REL-2", "PERF-1")
@pytest.mark.wp("P0-10")
@pytest.mark.xfail(strict=True, reason="spec:P0-10")
def test_every_v1_route_declares_policy_idempotency_and_pagination() -> None:
    """T-P0-10-15
    Over create_app()'s routes, nested routers included: every /v1 route has a policy with
    an auth mode; writes are idempotent or give a reason; `Page` responses are paginated and
    accept `cursor` and `limit`.
    """
    from typing import get_args  # noqa: PLC0415

    from tumnis.core.routing import (  # noqa: PLC0415
        AuthMode,
        policy_of,
        route_violations,
        walk_routes,
    )

    app = _app()
    v1 = [ctx for ctx in walk_routes(app) if ctx.path.startswith("/v1/")]
    paths = {ctx.path for ctx in v1}
    # The sweep sees routes behind nested include_router calls.
    assert {"/v1/audit", "/v1/dead-letters", "/v1/settings/workspace"} <= paths, sorted(paths)
    assert route_violations(app) == []
    for ctx in v1:
        if ctx.path == app.openapi_url:
            continue
        policy = policy_of(ctx)
        assert policy is not None, ctx.path
        assert policy.auth in get_args(AuthMode), (ctx.path, policy.auth)


@pytest.mark.req("SAAS-1")
@pytest.mark.wp("P0-10")
@pytest.mark.xfail(strict=True, reason="spec:P0-10")
def test_registry_flags_non_compliant_routes() -> None:
    """T-P0-10-16
    A synthetic app with a write lacking idempotency, a `list[...]` response, and a plain
    `APIRoute` yields three violations; a route without a policy cannot be declared.
    """
    from fastapi import APIRouter, FastAPI  # noqa: PLC0415
    from pydantic import BaseModel  # noqa: PLC0415

    from tumnis.core.routing import (  # noqa: PLC0415
        RoutePolicy,
        RouteWithoutPolicy,
        route_policy,
        route_violations,
        v1_router,
    )

    class Thing(BaseModel):
        name: str

    router = v1_router("synthetic", prefix="/things")

    @router.post("")
    @route_policy(RoutePolicy(auth="session"))
    async def create_thing(thing: Thing) -> Thing:
        return thing

    @router.get("")
    @route_policy(RoutePolicy(auth="session"))
    async def list_things() -> list[Thing]:
        return []

    with pytest.raises(RouteWithoutPolicy):

        @router.get("/{name}")
        async def get_thing(name: str) -> Thing:
            return Thing(name=name)

    v1 = APIRouter(prefix="/v1")
    v1.include_router(router)
    app = FastAPI()
    app.include_router(v1)

    @app.get("/v1/plain")
    async def plain() -> Thing:
        return Thing(name="plain")

    violations = route_violations(app)
    assert len(violations) == 3, violations
    assert any("POST /v1/things" in v and "idempot" in v for v in violations), violations
    assert any("GET /v1/things" in v and "list" in v for v in violations), violations
    assert any("/v1/plain" in v and "TumnisRoute" in v for v in violations), violations


@pytest.mark.req("SAAS-1")
@pytest.mark.wp("P0-10")
@pytest.mark.xfail(strict=True, reason="spec:P0-10")
def test_only_ops_paths_live_outside_v1() -> None:
    """T-P0-10-17
    The non-/v1 paths are only /health/live, /health/ready, /metrics, /mcp, /ws, /ws/runner
    and the static app.
    """
    from starlette.routing import Mount  # noqa: PLC0415

    from tumnis.core.routing import walk_routes  # noqa: PLC0415

    app = _app()
    outside = {
        ctx.path
        for ctx in walk_routes(app)
        if not ctx.path.startswith("/v1/")
        and not (isinstance(ctx.original_route, Mount) and ctx.original_route.name == "shell")
    }
    assert {"/health/live", "/health/ready"} <= outside, sorted(outside)
    assert outside <= OPS_PATHS, sorted(outside - OPS_PATHS)
