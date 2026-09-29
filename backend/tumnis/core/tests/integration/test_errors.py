"""One error model: every error is RFC 9457 problem+json with a stable `code` (P0-10, A3)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    import httpx

    from tests._pg import DbUrls
    from tumnis.core.tests.integration._demo import Demo

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _problem(response: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body: dict[str, Any] = response.json()
    assert body["status"] == status
    assert body["code"] == code
    assert body["type"].endswith(code)
    assert body["title"]
    return body


@pytest.mark.req("SAAS-1")
@pytest.mark.wp("P0-10")
async def test_every_error_is_problem_json_with_code(
    db: DbUrls, demo_app: Demo, demo_client: httpx.AsyncClient
) -> None:
    """T-P0-10-18
    404 (unknown route), 405 (wrong method), 409 (stale write), 413 (body too large), 422
    (invalid body) and 429 (rate limited) all answer `application/problem+json` with
    `type`, `title`, `status` and a stable `code`.
    """
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core.tests.integration._demo import client_at, principal_header  # noqa: PLC0415

    ws = make_workspace(db)
    who = principal_header(ws, uuid.uuid4())

    def key() -> dict[str, str]:
        return {**who, "Idempotency-Key": f"errors-{uuid.uuid4()}"}

    _problem(await demo_client.get("/v1/no-such-route", headers=who), 404, "not_found")
    _problem(await demo_client.delete("/v1/demo-items", headers=key()), 405, "method_not_allowed")

    created = await demo_client.post("/v1/demo-items", json={"title": "a"}, headers=key())
    assert created.status_code == 201, created.text
    item = created.json()
    path = f"/v1/demo-items/{item['id']}"
    moved = await demo_client.patch(path, json={"title": "b", "version": 1}, headers=key())
    assert moved.status_code == 200, moved.text
    stale = await demo_client.patch(path, json={"title": "c", "version": 1}, headers=key())
    assert _problem(stale, 409, "stale_version")["current"]["version"] == 2

    big = {"title": "x" * (1_048_576 + 1)}
    _problem(
        await demo_client.post("/v1/demo-items", json=big, headers=key()), 413, "body_too_large"
    )

    invalid = await demo_client.post("/v1/demo-items", json={"nope": 1}, headers=key())
    _problem(invalid, 422, "validation_error")

    async with client_at(demo_app.app, "198.51.100.7") as anonymous:
        responses = [await anonymous.get("/v1/demo-items") for _ in range(11)]
    _problem(responses[-1], 429, "rate_limited")
