"""Per-principal and per-address rate limits (429) and body-size limits (413) (P0-10, SEC-5)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx

    from tests._pg import DbUrls
    from tumnis.core.tests.integration._demo import Demo

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

MIB = 1_048_576


@pytest.mark.req("SEC-5")
@pytest.mark.wp("P0-10")
@pytest.mark.xfail(strict=True, reason="spec:P0-10")
async def test_rate_limit_returns_429_with_retry_after(
    db: DbUrls, demo_app: Demo, demo_client: httpx.AsyncClient
) -> None:
    """T-P0-10-12
    With the clock standing still, one principal's 51st request is 429 `rate_limited` with
    `Retry-After`; another principal is unaffected; after the clock advances it passes.
    """
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core.tests.integration._demo import principal_header  # noqa: PLC0415

    ws = make_workspace(db)
    first, second = principal_header(ws, uuid.uuid4()), principal_header(ws, uuid.uuid4())

    allowed = [await demo_client.get("/v1/demo-items", headers=first) for _ in range(50)]
    assert {r.status_code for r in allowed} == {200}
    limited = await demo_client.get("/v1/demo-items", headers=first)
    assert limited.status_code == 429, limited.text
    assert limited.headers["content-type"].startswith("application/problem+json")
    assert limited.json()["code"] == "rate_limited"
    retry_after = int(limited.headers["Retry-After"])
    assert retry_after >= 1

    assert (await demo_client.get("/v1/demo-items", headers=second)).status_code == 200

    demo_app.clock.advance(seconds=retry_after)
    assert (await demo_client.get("/v1/demo-items", headers=first)).status_code == 200


@pytest.mark.req("SEC-5")
@pytest.mark.wp("P0-10")
@pytest.mark.xfail(strict=True, reason="spec:P0-10")
async def test_anonymous_limit_is_per_address(demo_app: Demo) -> None:
    """T-P0-10-13
    Anonymous requests are limited per source address: the first address's 11th request is
    429; a second address still gets its own answer (401).
    """
    from tumnis.core.tests.integration._demo import client_at  # noqa: PLC0415

    async with (
        client_at(demo_app.app, "203.0.113.1") as one,
        client_at(demo_app.app, "203.0.113.2") as two,
    ):
        firsts = [await one.get("/v1/demo-items") for _ in range(10)]
        limited = await one.get("/v1/demo-items")
        other = await two.get("/v1/demo-items")

    assert {r.status_code for r in firsts} == {401}
    assert limited.status_code == 429, limited.text
    assert limited.json()["code"] == "rate_limited"
    assert "Retry-After" in limited.headers
    assert other.status_code == 401, other.text


@pytest.mark.req("SEC-5")
@pytest.mark.wp("P0-10")
@pytest.mark.xfail(strict=True, reason="spec:P0-10")
async def test_oversized_body_returns_413(
    db: DbUrls, demo_app: Demo, demo_client: httpx.AsyncClient
) -> None:
    """T-P0-10-14
    A `Content-Length` over the limit and a chunked body over the limit both give 413
    `body_too_large` before the handler runs.
    """
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core.tests.integration._demo import count_items, principal_header  # noqa: PLC0415

    who = principal_header(make_workspace(db), uuid.uuid4())

    def headers() -> dict[str, str]:
        return {
            **who,
            "Idempotency-Key": f"limits-{uuid.uuid4()}",
            "Content-Type": "application/json",
        }

    sized = await demo_client.post(
        "/v1/demo-items", content=b'{"title": "' + b"x" * MIB + b'"}', headers=headers()
    )

    async def chunks() -> AsyncIterator[bytes]:
        yield b'{"title": "'
        for _ in range(9):
            yield b"x" * (MIB // 8)
        yield b'"}'

    chunked = await demo_client.post("/v1/demo-items", content=chunks(), headers=headers())

    for response in (sized, chunked):
        assert response.status_code == 413, response.text
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["code"] == "body_too_large"
    assert "content-length" not in chunked.request.headers
    assert demo_app.state.posts == 0
    assert count_items(db) == 0

    small = await demo_client.post("/v1/demo-items", json={"title": "ok"}, headers=headers())
    assert small.status_code == 201, small.text
