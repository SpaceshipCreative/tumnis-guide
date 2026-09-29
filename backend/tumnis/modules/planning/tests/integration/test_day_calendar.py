"""The day calendar over REST (P1-10, FR-1.3, REL-6): `GET /v1/plan/{day}/calendar` gives
the working window in the workspace timezone, the day's events from every account and the
free blocks between them, computed on read and cached (`free_blocks` namespace)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.planning.tests.integration._day_calendar import add_events, day_calendar, utc

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests.fixtures import QueryCounter, WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

TUESDAY = date(2026, 3, 10)  # after the US spring change: New York is UTC-4, Los Angeles UTC-7


@pytest.mark.req("REL-6")
@pytest.mark.wp("P1-10")
@pytest.mark.xfail(strict=True, reason="spec:P1-10")
async def test_timezone_change_recomputes_blocks(
    app: FastAPI, session_client: SessionClient, workspace: WorkspaceHandle
) -> None:
    """T-P1-10-10
    The same stored events, read in New York and then, after the workspace moves to Los
    Angeles, again: the window (09:00 to 18:00 local) moves 3 hours later in UTC and the
    free blocks are recomputed against it. The first read fills the cache entry; the
    timezone change invalidates it.
    """
    from tumnis.core.cache import named_cache  # noqa: PLC0415
    from tumnis.modules.planning.api import FREE_BLOCKS_CACHE, day_calendar_key  # noqa: PLC0415

    await add_events(
        workspace.ctx,
        "avery@example.com",
        [(utc("2026-03-10T15:00:00Z"), utc("2026-03-10T16:00:00Z"))],
    )
    await add_events(
        workspace.ctx,
        "blake@example.org",
        [(utc("2026-03-10T18:00:00Z"), utc("2026-03-10T19:00:00Z"))],
    )
    cache = named_cache(FREE_BLOCKS_CACHE)
    key = day_calendar_key(workspace.id, TUESDAY)

    first = await day_calendar(session_client, TUESDAY)
    assert first.status_code == 200, first.text
    new_york = first.json()
    assert new_york["timezone"] == "America/New_York"
    assert new_york["window"] == {
        "start": "2026-03-10T13:00:00Z",
        "end": "2026-03-10T22:00:00Z",
    }
    assert [(b["start"], b["end"], b["minutes"]) for b in new_york["free_blocks"]] == [
        ("2026-03-10T13:00:00Z", "2026-03-10T15:00:00Z", 120),
        ("2026-03-10T16:00:00Z", "2026-03-10T18:00:00Z", 120),
        ("2026-03-10T19:00:00Z", "2026-03-10T22:00:00Z", 180),
    ]
    assert await cache.get(key) is not None

    settings = (await session_client.get("/v1/settings/workspace")).json()
    moved = await session_client.put(
        "/v1/settings/workspace",
        json={"timezone": "America/Los_Angeles", "version": settings["version"]},
    )
    assert moved.status_code == 200, moved.text
    assert await cache.get(key) is None

    second = await day_calendar(session_client, TUESDAY)
    assert second.status_code == 200, second.text
    los_angeles = second.json()
    assert los_angeles["timezone"] == "America/Los_Angeles"
    assert utc(los_angeles["window"]["start"]) - utc(new_york["window"]["start"]) == timedelta(
        hours=3
    )
    assert utc(los_angeles["window"]["end"]) - utc(new_york["window"]["end"]) == timedelta(hours=3)
    assert [(b["start"], b["end"], b["minutes"]) for b in los_angeles["free_blocks"]] == [
        ("2026-03-10T16:00:00Z", "2026-03-10T18:00:00Z", 120),
        ("2026-03-10T19:00:00Z", "2026-03-11T01:00:00Z", 360),
    ]
    assert await cache.get(key) is not None


@pytest.mark.req("FR-1.3")
@pytest.mark.wp("P1-10")
@pytest.mark.xfail(strict=True, reason="spec:P1-10")
async def test_strip_endpoint_shape_and_query_count(
    app: FastAPI,
    session_client: SessionClient,
    workspace: WorkspaceHandle,
    query_counter: QueryCounter,
) -> None:
    """T-P1-10-11
    The response is `{timezone, window, events: [{title, start, end, busy, account}],
    free_blocks: [{start, end, minutes}]}`; events from both accounts are listed, a free
    (transparent) event does not block time. A day with one event and a day with 24 events
    across two accounts cost the same number of SQL statements.
    """
    from tumnis.core import db as core_db  # noqa: PLC0415

    wednesday, thursday, friday = (TUESDAY + timedelta(days=n) for n in (1, 2, 3))
    await add_events(
        workspace.ctx,
        "avery@example.com",
        [
            (utc("2026-03-11T14:00:00Z"), utc("2026-03-11T15:00:00Z")),
            (utc("2026-03-11T17:00:00Z"), utc("2026-03-11T18:00:00Z"), False),
        ],
    )
    await add_events(
        workspace.ctx,
        "blake@example.org",
        [(utc("2026-03-11T14:30:00Z"), utc("2026-03-11T15:30:00Z"))],
    )
    await add_events(
        workspace.ctx,
        "avery@example.com",
        [(utc("2026-03-12T13:00:00Z"), utc("2026-03-12T13:10:00Z"))],
    )
    many = [
        (
            utc("2026-03-13T13:00:00Z") + timedelta(minutes=20 * n),
            utc("2026-03-13T13:10:00Z") + timedelta(minutes=20 * n),
        )
        for n in range(24)
    ]
    await add_events(workspace.ctx, "avery@example.com", many[::2])
    await add_events(workspace.ctx, "blake@example.org", many[1::2])

    response = await day_calendar(session_client, wednesday)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"timezone", "window", "events", "free_blocks"}
    assert body["timezone"] == "America/New_York"
    assert body["window"] == {"start": "2026-03-11T13:00:00Z", "end": "2026-03-11T22:00:00Z"}
    assert [set(event) for event in body["events"]] == [
        {"title", "start", "end", "busy", "account"}
    ] * 3
    assert [(e["start"], e["busy"], e["account"]) for e in body["events"]] == [
        ("2026-03-11T14:00:00Z", True, "avery@example.com"),
        ("2026-03-11T14:30:00Z", True, "blake@example.org"),
        ("2026-03-11T17:00:00Z", False, "avery@example.com"),
    ]
    assert body["free_blocks"] == [
        {"start": "2026-03-11T13:00:00Z", "end": "2026-03-11T14:00:00Z", "minutes": 60},
        {"start": "2026-03-11T15:30:00Z", "end": "2026-03-11T22:00:00Z", "minutes": 390},
    ]

    query_counter.watch(core_db.app_engine())
    counts: dict[date, int] = {}
    for day in (thursday, friday):
        query_counter.reset()
        answered = await day_calendar(session_client, day)
        assert answered.status_code == 200, answered.text
        counts[day] = query_counter.count
    assert len((await day_calendar(session_client, friday)).json()["events"]) == 24
    assert counts[thursday] == counts[friday], query_counter.statements
