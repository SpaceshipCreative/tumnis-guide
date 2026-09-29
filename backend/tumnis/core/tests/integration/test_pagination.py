"""Keyset pagination: every row present at the start comes back exactly once, however rows
are inserted between page fetches (P0-10, PERF-1). Driven through the demo router's
`GET /v1/demo-items` with the real `paginate()`."""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

if TYPE_CHECKING:
    import httpx

    from tests._pg import DbUrls
    from tumnis.core.tests.integration._demo import Demo

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

# A narrow range, so due dates tie often and the id tiebreak is exercised.
DUE = st.one_of(st.none(), st.dates(min_value=date(2026, 3, 1), max_value=date(2026, 3, 6)))


@dataclass(frozen=True)
class Insert:
    dues: tuple[date | None, ...]


OPS = st.lists(
    st.one_of(st.just("page"), st.lists(DUE, min_size=1, max_size=5).map(tuple).map(Insert)),
    max_size=40,
)
PROPERTY = settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)


async def _walk(  # noqa: PLR0917
    db: DbUrls,
    demo: Demo,
    client: httpx.AsyncClient,
    sort: str,
    initial: list[date | None],
    ops: list[Any],
    limit: int,
) -> tuple[list[uuid.UUID], list[str]]:
    """A fresh workspace with `initial` rows; page through while `ops` insert more; then
    page to the end. Returns (initial ids, returned ids). The clock moves a second per
    fetch, so the walk stays inside the principal's rate limit."""
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core.tests.integration._demo import insert_items, principal_header  # noqa: PLC0415

    ws = make_workspace(db, "P")
    who = principal_header(ws, uuid.uuid4())
    start = insert_items(db, ws, initial)
    returned: list[str] = []
    cursor: str | None = None
    done = False

    async def fetch() -> None:
        nonlocal cursor, done
        demo.clock.advance(seconds=1)
        params: dict[str, Any] = {"limit": limit, "sort": sort}
        if cursor is not None:
            params["cursor"] = cursor
        response = await client.get("/v1/demo-items", params=params, headers=who)
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["items"]) <= limit
        returned.extend(item["id"] for item in body["items"])
        cursor = body["next_cursor"]
        done = cursor is None

    for op in ops:
        if isinstance(op, Insert):
            insert_items(db, ws, op.dues)
        elif not done:
            await fetch()
    guard = 0
    while not done:
        await fetch()
        guard += 1
        assert guard < 1000, "pagination never ended"
    return start, returned


@pytest.mark.req("PERF-1")
@pytest.mark.wp("P0-10")
@PROPERTY
@given(initial=st.integers(0, 60), ops=OPS, limit=st.integers(1, 7))
async def test_every_row_once_while_rows_are_inserted(  # noqa: PLR0917
    db: DbUrls, demo_app: Demo, demo_client: httpx.AsyncClient, initial: int, ops: Any, limit: int
) -> None:
    """T-P0-10-09
    Interleaved page fetches and inserts, ordered by id: every row present at the start is
    returned exactly once, and no row twice.
    """
    start, returned = await _walk(db, demo_app, demo_client, "id", [None] * initial, ops, limit)
    assert len(returned) == len(set(returned)), "a row came back twice"
    assert {str(i) for i in start} <= set(returned), "a row present at the start was skipped"


@pytest.mark.req("PERF-1")
@pytest.mark.wp("P0-10")
@PROPERTY
@given(initial=st.lists(DUE, max_size=60), ops=OPS, limit=st.integers(1, 7))
async def test_every_row_once_with_nullable_sort_key(  # noqa: PLR0917
    db: DbUrls,
    demo_app: Demo,
    demo_client: httpx.AsyncClient,
    initial: list[date | None],
    ops: Any,
    limit: int,
) -> None:
    """T-P0-10-10
    The same property ordering by (due_on NULLS LAST, id); pages come back in that order.
    """
    start, returned = await _walk(db, demo_app, demo_client, "due_on", initial, ops, limit)
    assert len(returned) == len(set(returned)), "a row came back twice"
    assert {str(i) for i in start} <= set(returned), "a row present at the start was skipped"
    by_id = dict(zip((str(i) for i in start), initial, strict=True))
    seen = [(by_id[i] or date.max, i) for i in returned if i in by_id]
    assert seen == sorted(seen, key=lambda pair: (pair[0], uuid.UUID(pair[1])))


def _b64(value: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")


@pytest.mark.req("PERF-1")
@pytest.mark.wp("P0-10")
async def test_malformed_cursor_is_400(
    db: DbUrls, demo_app: Demo, demo_client: httpx.AsyncClient
) -> None:
    """T-P0-10-11
    Garbage, truncated and wrong-version cursors (and one with the wrong number of sort
    keys) give 400 `invalid_cursor`.
    """
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core.tests.integration._demo import insert_items, principal_header  # noqa: PLC0415

    ws = make_workspace(db)
    who = principal_header(ws, uuid.uuid4())
    insert_items(db, ws, [None, None, None])
    first = await demo_client.get("/v1/demo-items", params={"limit": 2}, headers=who)
    assert first.status_code == 200, first.text
    good = first.json()["next_cursor"]
    assert good

    bad = {
        "garbage": ("id", "not-a-cursor!"),
        "truncated": ("id", good[: len(good) // 2]),
        "wrong version": ("id", _b64({"v": 2, "k": [], "id": str(uuid.uuid4())})),
        "not an id": ("id", _b64({"v": 1, "k": [], "id": "nope"})),
        "wrong key count": ("due_on", good),
    }
    for name, (sort, cursor) in bad.items():
        response = await demo_client.get(
            "/v1/demo-items", params={"sort": sort, "cursor": cursor}, headers=who
        )
        assert response.status_code == 400, (name, response.text)
        assert response.headers["content-type"].startswith("application/problem+json"), name
        assert response.json()["code"] == "invalid_cursor", name
