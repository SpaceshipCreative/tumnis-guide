"""A concrete path is matched before a templated one (OpenAPI's Paths Object), for the route
too, not only for the `Allow` header (P1-11, found by the P0-11 fuzzer): `GET
/v1/plan/replan` answers 405 `Allow: POST`, because `/v1/plan/replan` serves only POST,
instead of running `GET /v1/plan/{day}` with `day="replan"` (whose auth answered 403)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from tumnis.core.errors import install_problem_handlers
from tumnis.core.routing import RoutePolicy, route_policy, v1_router

pytestmark = [pytest.mark.req("SAAS-1"), pytest.mark.wp("P1-11")]


def _app() -> FastAPI:
    plan = v1_router("concrete-demo", prefix="/plan")

    @plan.post("/replan", status_code=202)
    @route_policy(RoutePolicy(auth="none", idempotent=False, not_idempotent_reason="test"))
    async def replan() -> dict[str, str]:
        return {"queued": "yes"}

    @plan.get("/{day}")
    @route_policy(RoutePolicy(auth="session"))
    async def get_plan(day: str) -> dict[str, str]:
        return {"day": day}

    settings = v1_router("concrete-demo", prefix="/settings")

    @settings.get("/workspace")
    @route_policy(RoutePolicy(auth="none"))
    async def get_workspace() -> dict[str, str]:
        return {"workspace": "concrete"}

    @settings.get("/{section}")
    @route_policy(RoutePolicy(auth="none"))
    async def get_section(section: str) -> dict[str, str]:
        return {"section": section}

    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(plan, prefix="/v1")
    app.include_router(settings, prefix="/v1")
    return app


async def _send(method: str, url: str, **kwargs: Any) -> httpx.Response:
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as http:
        return await http.request(method, url, **kwargs)


@pytest.mark.parametrize("method", ["GET", "HEAD"])
async def test_a_method_the_concrete_path_lacks_is_405(method: str) -> None:
    response = await _send(method, "/v1/plan/replan")
    assert response.status_code == 405, response.text
    assert response.headers["allow"] == "POST"


async def test_the_concrete_method_still_runs() -> None:
    response = await _send("POST", "/v1/plan/replan")
    assert response.status_code == 202, response.text


async def test_another_value_still_reaches_the_templated_route() -> None:
    response = await _send("GET", "/v1/plan/2026-03-09")
    assert response.status_code == 401, response.text  # its own auth, as before


async def test_a_concrete_path_serving_the_method_is_unchanged() -> None:
    """`/v1/settings/workspace` and `/v1/settings/{section}` both serve GET: the concrete
    route answers its path, the templated one every other value."""
    response = await _send("GET", "/v1/settings/workspace")
    assert response.status_code == 200, response.text
    assert response.json() == {"workspace": "concrete"}
    other = await _send("GET", "/v1/settings/modules")
    assert other.json() == {"section": "modules"}
