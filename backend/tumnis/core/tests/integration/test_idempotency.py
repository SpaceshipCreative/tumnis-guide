"""Writes carry an Idempotency-Key; a retry replays the stored response for 24 hours (P0-10,
REL-2). Driven through the demo router's `POST /v1/demo-items` (see `_demo.py`)."""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx

    from tests._pg import DbUrls
    from tumnis.core.tests.integration._demo import Demo

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

REPLAYED = "Idempotent-Replayed"


def _headers(who: dict[str, str], key: str) -> dict[str, str]:
    return {**who, "Idempotency-Key": key}


def _who(db: DbUrls) -> dict[str, str]:
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core.tests.integration._demo import principal_header  # noqa: PLC0415

    return principal_header(make_workspace(db), uuid.uuid4())


@pytest.mark.req("REL-2")
@pytest.mark.wp("P0-10")
async def test_same_key_same_body_replays_stored_response(
    db: DbUrls, demo_app: Demo, demo_client: httpx.AsyncClient
) -> None:
    """T-P0-10-01
    Second call returns the same status and body with `Idempotent-Replayed: true`; the
    handler ran once; one row in `demo_items`.
    """
    from tumnis.core.tests.integration._demo import count_items  # noqa: PLC0415

    headers = _headers(_who(db), "replay-key-0001")
    first = await demo_client.post(
        "/v1/demo-items", json={"title": "a", "due_on": "2026-03-10"}, headers=headers
    )
    # The same body with its keys in another order is the same request.
    second = await demo_client.post(
        "/v1/demo-items", json={"due_on": "2026-03-10", "title": "a"}, headers=headers
    )

    assert first.status_code == 201, first.text
    assert REPLAYED not in first.headers
    assert second.status_code == 201, second.text
    assert second.headers[REPLAYED] == "true"
    assert second.json() == first.json()
    assert second.headers["content-type"] == first.headers["content-type"]
    assert demo_app.state.posts == 1
    assert count_items(db) == 1


@pytest.mark.req("REL-2")
@pytest.mark.wp("P0-10")
async def test_same_key_different_body_is_rejected(
    db: DbUrls, demo_app: Demo, demo_client: httpx.AsyncClient
) -> None:
    """T-P0-10-02
    The same key with a different body is 422 `idempotency_mismatch`; the handler did not
    run again.
    """
    headers = _headers(_who(db), "mismatch-key-01")
    first = await demo_client.post("/v1/demo-items", json={"title": "a"}, headers=headers)
    assert first.status_code == 201, first.text

    other = await demo_client.post("/v1/demo-items", json={"title": "b"}, headers=headers)
    assert other.status_code == 422, other.text
    assert other.headers["content-type"].startswith("application/problem+json")
    assert other.json()["code"] == "idempotency_mismatch"
    assert demo_app.state.posts == 1


@pytest.mark.req("REL-2")
@pytest.mark.wp("P0-10")
async def test_key_expires_after_24_hours(
    db: DbUrls, demo_app: Demo, demo_client: httpx.AsyncClient
) -> None:
    """T-P0-10-03
    With the FixedClock advanced 23 h 59 m the same key replays; at 24 h + 1 s it runs the
    handler again.
    """
    headers = _headers(_who(db), "expiry-key-0001")
    body = {"title": "a"}
    first = await demo_client.post("/v1/demo-items", json=body, headers=headers)
    assert first.status_code == 201, first.text

    demo_app.clock.advance(hours=23, minutes=59)
    replayed = await demo_client.post("/v1/demo-items", json=body, headers=headers)
    assert replayed.headers.get(REPLAYED) == "true"
    assert replayed.json() == first.json()
    assert demo_app.state.posts == 1

    demo_app.clock.advance(minutes=1, seconds=1)
    again = await demo_client.post("/v1/demo-items", json=body, headers=headers)
    assert again.status_code == 201, again.text
    assert REPLAYED not in again.headers
    assert again.json()["id"] != first.json()["id"]
    assert demo_app.state.posts == 2


@pytest.mark.req("REL-2")
@pytest.mark.wp("P0-10")
async def test_concurrent_requests_with_one_key_execute_once(
    db: DbUrls, demo_app: Demo, demo_client: httpx.AsyncClient
) -> None:
    """T-P0-10-04
    Given the POST handler sleeps 200 ms inside its transaction, when two requests with the
    same key and body run together, then the second waits on the unique index until the
    first commits and replays; one row is created.
    """
    from tumnis.core.tests.integration._demo import count_items  # noqa: PLC0415

    demo_app.state.delay_s = 0.2
    headers = _headers(_who(db), "concurrent-key-1")
    responses = await asyncio.gather(
        demo_client.post("/v1/demo-items", json={"title": "a"}, headers=headers),
        demo_client.post("/v1/demo-items", json={"title": "a"}, headers=headers),
    )

    assert [r.status_code for r in responses] == [201, 201], [r.text for r in responses]
    assert sorted(r.headers.get(REPLAYED, "") for r in responses) == ["", "true"]
    assert responses[0].json() == responses[1].json()
    assert demo_app.state.posts == 1
    assert count_items(db) == 1


@pytest.mark.req("REL-2")
@pytest.mark.wp("P0-10")
async def test_write_without_key_is_rejected(
    db: DbUrls, demo_app: Demo, demo_client: httpx.AsyncClient
) -> None:
    """T-P0-10-05
    A write without an Idempotency-Key (or with a malformed one) is 400
    `idempotency_key_required`; the handler does not run.
    """
    who = _who(db)
    missing = await demo_client.post("/v1/demo-items", json={"title": "a"}, headers=who)
    malformed = await demo_client.post(
        "/v1/demo-items", json={"title": "a"}, headers=_headers(who, "short")
    )

    for response in (missing, malformed):
        assert response.status_code == 400, response.text
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["code"] == "idempotency_key_required"
    assert demo_app.state.posts == 0


@pytest.mark.req("REL-2")
@pytest.mark.wp("P0-10")
async def test_server_error_is_not_stored(
    db: DbUrls, demo_app: Demo, demo_client: httpx.AsyncClient
) -> None:
    """T-P0-10-06
    A handler that raises once then succeeds: the first call is 500 and stores nothing (its
    row is rolled back), the retry with the same key runs and succeeds.
    """
    from tumnis.core.tests.integration._demo import count_items  # noqa: PLC0415

    demo_app.state.fail_next = 1
    headers = _headers(_who(db), "server-error-key")
    failed = await demo_client.post("/v1/demo-items", json={"title": "a"}, headers=headers)
    assert failed.status_code == 500, failed.text
    assert count_items(db) == 0

    retried = await demo_client.post("/v1/demo-items", json={"title": "a"}, headers=headers)
    assert retried.status_code == 201, retried.text
    assert REPLAYED not in retried.headers
    assert demo_app.state.posts == 2
    assert count_items(db) == 1


@pytest.mark.req("REL-2", "SAAS-1")
@pytest.mark.wp("P0-10")
async def test_keys_are_scoped_per_principal_and_workspace(
    db: DbUrls, demo_app: Demo, demo_client: httpx.AsyncClient
) -> None:
    """T-P0-10-07
    The same key from two principals of one workspace runs twice, and from a principal of
    another workspace a third time; none of them replays.
    """
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core.tests.integration._demo import count_items, principal_header  # noqa: PLC0415

    ws_a, ws_b = make_workspace(db, "A"), make_workspace(db, "B")
    user = uuid.uuid4()
    callers = [
        principal_header(ws_a, user),
        principal_header(ws_a, uuid.uuid4()),
        principal_header(ws_b, user),
    ]
    responses = [
        await demo_client.post(
            "/v1/demo-items", json={"title": "a"}, headers=_headers(who, "shared-key-0001")
        )
        for who in callers
    ]

    assert [r.status_code for r in responses] == [201, 201, 201], [r.text for r in responses]
    assert all(REPLAYED not in r.headers for r in responses)
    assert len({r.json()["id"] for r in responses}) == 3
    assert demo_app.state.posts == 3
    assert count_items(db) == 3
