"""Settings > Working hours over REST and the day calendar's cache invalidation (P1-10,
FR-4.7, FR-1.3): the defaults, a versioned save that moves the window and drops the cached
days, the refusals, and `calendar.synced` dropping the cache."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._pg import OWNER
from tests.fixtures import make_envelope
from tumnis.modules.planning.tests.integration._day_calendar import add_events, day_calendar, utc

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

MONDAY = date(2026, 3, 9)
DEFAULT_WEEK = [{"weekday": d, "start": "09:00", "end": "18:00"} for d in range(5)]


def _audit_actions(db: DbUrls) -> list[tuple[str, Any]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        rows = conn.execute("SELECT action, details FROM audit_log ORDER BY seq").fetchall()
    return [(action, details) for action, details in rows]


@pytest.mark.req("FR-4.7")
@pytest.mark.wp("P1-10")
async def test_saved_hours_move_the_window_and_drop_cached_days(
    app: FastAPI, session_client: SessionClient, workspace: WorkspaceHandle, db: DbUrls
) -> None:
    """A new workspace reads Monday to Friday 09:00 to 18:00 at version 0. Saving Monday
    10:00 to 16:00 answers the week at version 1, audits `settings.changed` with the
    section and weekday, drops the cached Monday, and the next read of Monday has the new
    window. Saving the same hours again changes nothing and bumps nothing."""
    from tumnis.core.cache import named_cache  # noqa: PLC0415
    from tumnis.modules.planning.api import FREE_BLOCKS_CACHE, day_calendar_key  # noqa: PLC0415

    read = await session_client.get("/v1/settings/working-hours")
    assert read.status_code == 200, read.text
    assert read.json() == {"days": DEFAULT_WEEK, "version": 0}

    before = await day_calendar(session_client, MONDAY)
    assert before.json()["window"] == {
        "start": "2026-03-09T13:00:00Z",
        "end": "2026-03-09T22:00:00Z",
    }
    cache = named_cache(FREE_BLOCKS_CACHE)
    assert await cache.get(day_calendar_key(workspace.id, MONDAY)) is not None

    monday = {"weekday": 0, "start": "10:00", "end": "16:00"}
    saved = await session_client.put(
        "/v1/settings/working-hours", json={"days": [monday], "version": 0}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json() == {"days": [monday, *DEFAULT_WEEK[1:]], "version": 1}
    assert await cache.get(day_calendar_key(workspace.id, MONDAY)) is None
    assert ("settings.changed", {"section": "working-hours", "fields": ["weekday_0"]}) in (
        _audit_actions(db)
    )

    after = await day_calendar(session_client, MONDAY)
    assert after.json()["window"] == {
        "start": "2026-03-09T14:00:00Z",
        "end": "2026-03-09T20:00:00Z",
    }

    again = await session_client.put(
        "/v1/settings/working-hours", json={"days": [monday], "version": 1}
    )
    assert again.status_code == 200, again.text
    assert again.json()["version"] == 1


@pytest.mark.req("FR-4.7")
@pytest.mark.wp("P1-10")
async def test_stale_or_invalid_hours_are_refused(
    app: FastAPI, session_client: SessionClient, workspace: WorkspaceHandle
) -> None:
    """A save at an old version is 409 `stale_version` with the current week; an end at
    or before the start, or a weekday named twice, is 422 `validation_error`; nothing is
    stored."""
    first = await session_client.put(
        "/v1/settings/working-hours",
        json={"days": [{"weekday": 1, "start": "08:00", "end": "17:00"}], "version": 0},
    )
    assert first.status_code == 200, first.text

    stale = await session_client.put(
        "/v1/settings/working-hours",
        json={"days": [{"weekday": 2, "start": "08:00", "end": "17:00"}], "version": 0},
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "stale_version"
    assert stale.json()["current"]["version"] == 1

    for days in (
        [{"weekday": 3, "start": "12:00", "end": "11:00"}],
        [{"weekday": 3, "start": "12:00", "end": "12:00"}],
        [
            {"weekday": 3, "start": "09:00", "end": "12:00"},
            {"weekday": 3, "start": "13:00", "end": "17:00"},
        ],
    ):
        refused = await session_client.put(
            "/v1/settings/working-hours", json={"days": days, "version": 1}
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["code"] == "validation_error"

    week = (await session_client.get("/v1/settings/working-hours")).json()
    assert week["version"] == 1
    assert week["days"][1] == {"weekday": 1, "start": "08:00", "end": "17:00"}
    assert week["days"][3] == {"weekday": 3, "start": "09:00", "end": "18:00"}


@pytest.mark.req("FR-4.7", "FR-1.3")
@pytest.mark.wp("P1-10")
async def test_saved_hours_and_timezone_announce_a_settings_change(
    app: FastAPI,
    session_client: SessionClient,
    workspace: WorkspaceHandle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Saving new hours, or a new workspace timezone, announces `settings` for the
    workspace on the live socket so other browsers refetch the hours and the day's free
    blocks; a save that changes nothing announces nothing."""
    from tumnis.core import live  # noqa: PLC0415

    marks: list[tuple[str, Any]] = []
    real = live.mark_changed

    def spy(session: Any, entity: str, id: Any) -> None:
        marks.append((entity, id))
        real(session, entity, id)

    monkeypatch.setattr(live, "mark_changed", spy)

    monday = {"weekday": 0, "start": "10:00", "end": "16:00"}
    url = "/v1/settings/working-hours"
    saved = await session_client.put(url, json={"days": [monday], "version": 0})
    assert saved.status_code == 200, saved.text
    assert marks == [("settings", workspace.id)]

    marks.clear()
    again = await session_client.put(url, json={"days": [monday], "version": 1})
    assert again.status_code == 200, again.text
    assert marks == []

    current = (await session_client.get("/v1/settings/workspace")).json()
    moved = await session_client.put(
        "/v1/settings/workspace",
        json={"timezone": "Europe/Berlin", "version": current["version"]},
        headers={"Idempotency-Key": "planning-timezone-live"},
    )
    assert moved.status_code == 200, moved.text
    assert ("settings", workspace.id) in marks


@pytest.mark.req("FR-1.3")
@pytest.mark.wp("P1-10")
async def test_calendar_synced_drops_cached_days(
    app: FastAPI, session_client: SessionClient, workspace: WorkspaceHandle
) -> None:
    """After a sync (`calendar.synced`), the planning subscriber drops every cached day
    of the workspace; the next read sees the events the sync stored."""
    from tumnis.core.cache import named_cache  # noqa: PLC0415
    from tumnis.modules.planning.api import FREE_BLOCKS_CACHE, day_calendar_key  # noqa: PLC0415
    from tumnis.modules.planning.events import drop_day_calendars  # noqa: PLC0415

    empty = await day_calendar(session_client, MONDAY)
    assert empty.json()["events"] == []
    key = day_calendar_key(workspace.id, MONDAY)
    cache = named_cache(FREE_BLOCKS_CACHE)
    assert await cache.get(key) is not None

    await add_events(
        workspace.ctx,
        "avery@example.com",
        [(utc("2026-03-09T14:00:00Z"), utc("2026-03-09T15:00:00Z"))],
    )
    await drop_day_calendars(
        make_envelope(
            "calendar.synced",
            {
                "connection_id": "01890000-0000-7000-8000-0000000000d1",
                "window": {"start": "2026-03-08T05:00:00Z", "end": "2026-03-24T04:00:00Z"},
            },
            workspace,
        )
    )
    assert await cache.get(key) is None
    synced = (await day_calendar(session_client, MONDAY)).json()
    assert [event["account"] for event in synced["events"]] == ["avery@example.com"]
    assert synced["free_blocks"][0] == {
        "start": "2026-03-09T13:00:00Z",
        "end": "2026-03-09T14:00:00Z",
        "minutes": 60,
    }
