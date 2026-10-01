"""The `Allow` header of a 405 (P0-10, P1-11): every method any route serves at the path,
and a concrete path is matched before a templated one (OpenAPI's Paths Object), so
`/v1/plan/replan` (POST) does not advertise the GET of `/v1/plan/{day}`."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from tumnis.core.errors import _allowed_methods


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/v1/plan/{day}")
    async def get_plan(day: str) -> str:
        return day

    @app.post("/v1/plan/replan")
    async def replan() -> None:
        return None

    @app.put("/v1/items/{item}")
    async def put_item(item: str) -> str:
        return item

    @app.get("/v1/items/{item}")
    async def get_item(item: str) -> str:
        return item

    return app


def _allow(app: FastAPI, path: str) -> str | None:
    scope = {"type": "http", "method": "OPTIONS", "path": path, "headers": [], "app": app}
    return _allowed_methods(Request(scope))


@pytest.mark.req("SAAS-1")
@pytest.mark.wp("P1-11")
@pytest.mark.parametrize(
    ("path", "allow"),
    [
        pytest.param("/v1/plan/replan", "POST", id="concrete_first"),
        pytest.param("/v1/plan/2026-03-09", "GET", id="templated"),
        pytest.param("/v1/items/x", "GET, PUT", id="methods_on_separate_routes"),
        pytest.param("/v1/nothing", None, id="no_route"),
    ],
)
def test_allow_names_the_methods_served_at_the_path(path: str, allow: str | None) -> None:
    assert _allow(_app(), path) == allow
