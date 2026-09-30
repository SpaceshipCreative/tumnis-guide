"""TumnisRoute refuses a NUL character in a request's strings with 422 `validation_error`
before the handler runs (bug: the P0-11 contract fuzzer). PostgreSQL text cannot hold NUL,
so a handler that passed such a string to the database answered 500 (psycopg DataError).
The check covers path parameters, query parameters (names and values) and JSON bodies
(values and keys, at any depth); an escaped backslash followed by `u0000` is not a NUL."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from tumnis.core.errors import install_problem_handlers
from tumnis.core.routing import RoutePolicy, route_policy, v1_router

NUL = "\x00"

pytestmark = [pytest.mark.req("SAAS-1"), pytest.mark.wp("P0-10")]


class ItemIn(BaseModel):
    name: str
    labels: dict[str, Any] = {}
    tags: list[str] = []


def _app() -> FastAPI:
    router = v1_router("nul-demo", prefix="/items")

    @router.post("")
    @route_policy(RoutePolicy(auth="none", idempotent=False, not_idempotent_reason="test"))
    async def create_item(body: ItemIn) -> dict[str, Any]:
        return body.model_dump()

    @router.get("/{slug}")
    @route_policy(RoutePolicy(auth="none"))
    async def get_item(slug: str, q: str = "") -> dict[str, str]:
        return {"slug": slug, "q": q}

    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(router, prefix="/v1")
    return app


async def _send(method: str, url: str, **kwargs: Any) -> httpx.Response:
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as http:
        return await http.request(method, url, **kwargs)


def _assert_refused(response: httpx.Response) -> None:
    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "validation_error"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"name": f"a{NUL}b"}, id="field"),
        pytest.param({"name": "ok", "tags": ["fine", NUL]}, id="list-item"),
        pytest.param({"name": "ok", "labels": {"deep": {"deeper": [f"x{NUL}"]}}}, id="nested"),
        pytest.param({"name": "ok", "labels": {f"key{NUL}": 1}}, id="object-key"),
        pytest.param({"name": "ok", f"extra{NUL}": 1}, id="ignored-extra-key"),
    ],
)
async def test_nul_in_json_body_is_422(body: dict[str, Any]) -> None:
    _assert_refused(await _send("POST", "/v1/items", json=body))


async def test_nul_in_utf16_json_body_is_422() -> None:
    """json.loads (and so FastAPI) reads UTF-16 JSON too; the check must see it."""
    content = '{"name": "a\\u0000b"}'.encode("utf-16")
    headers = {"content-type": "application/json"}
    _assert_refused(await _send("POST", "/v1/items", content=content, headers=headers))


async def test_nul_in_query_value_is_422() -> None:
    _assert_refused(await _send("GET", "/v1/items/one", params={"q": f"a{NUL}"}))


async def test_nul_in_query_name_is_422() -> None:
    _assert_refused(await _send("GET", "/v1/items/one?bad%00name=1"))


async def test_nul_in_path_param_is_422() -> None:
    _assert_refused(await _send("GET", "/v1/items/on%00e"))


LONG_KEY = "k" * 10_000


def _long_key_over_long_array() -> dict[str, Any]:
    return {"name": "ok", "labels": {LONG_KEY: [0] * 9_999 + [NUL]}}


def test_walk_keeps_parent_links_not_path_copies() -> None:
    """A long key over a long array holding one NUL: the walk keeps each child's location
    as a reference to its parent, not a copy of the whole path, so its memory stays small
    (a copy per element would be key length x elements, here ~100 MB). Measured on the
    walk alone, without the app or the HTTP client."""
    import tracemalloc  # noqa: PLC0415

    from tumnis.core.routing import _nul_in_json  # noqa: PLC0415  # the walk under test

    document = _long_key_over_long_array()
    tracemalloc.start()
    try:
        where = _nul_in_json(document)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert where == f"body.labels.{LONG_KEY}.9999"
    assert peak < 5_000_000, f"peak {peak:,} bytes"


async def test_long_key_over_a_long_array_is_422_with_a_short_detail() -> None:
    """End to end, the same body is refused, and the location in the detail is cut short
    rather than echoing the caller's 10,000-character key."""
    response = await _send("POST", "/v1/items", json=_long_key_over_long_array())
    _assert_refused(response)
    assert len(response.json()["detail"]) <= 300


async def test_escaped_backslash_before_u0000_is_not_a_nul() -> None:
    """`\\\\u0000` in JSON is a backslash followed by the text `u0000`: allowed."""
    content = b'{"name": "a\\\\u0000b"}'
    headers = {"content-type": "application/json"}
    response = await _send("POST", "/v1/items", content=content, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "a\\u0000b"


async def test_malformed_json_is_still_fastapis_validation_error() -> None:
    """A body the check cannot parse is left to FastAPI, which answers 422 as before."""
    content = b'{"name": "a\\u0000'
    headers = {"content-type": "application/json"}
    _assert_refused(await _send("POST", "/v1/items", content=content, headers=headers))
