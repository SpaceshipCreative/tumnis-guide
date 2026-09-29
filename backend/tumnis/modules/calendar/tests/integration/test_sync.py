"""Calendar sync on P0-12's canonical tables: idempotent upserts, cancelled events,
several accounts, a sync resumed from its cursor after a kill, a revoked grant and the
`calendar.synced` event (P1-09, FR-14.3, FR-14.4, FR-1.3)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tumnis.modules.calendar.tests.integration._calendar import (
    ACCOUNTS,
    T0,
    connect,
    outbox,
    recording,
    refresh_token,
    rows,
    scalar,
)

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._pg import DbUrls
    from tests.fixtures import WorkerKillerFactory, WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.calendar.adapters.fake import FakeGoogleCalendar
    from tumnis.modules.integrations.api import SyncPage

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

DAY_START = datetime(2026, 3, 9, 4, 0, tzinfo=UTC)  # 2026-03-09 00:00 America/New_York
DAY_END = DAY_START + timedelta(days=1)
WEEK_END = DAY_START + timedelta(days=14)
PAGE_COUNT = 3  # account a's primary calendar: pages 1 to 3


async def _sync(workspace: WorkspaceHandle, connection_id: uuid.UUID) -> dict[str, Any]:
    from dbos import SetWorkflowID  # noqa: PLC0415

    from tumnis.modules.calendar.workflows import connector_sync  # noqa: PLC0415

    with SetWorkflowID(f"test-sync-{uuid.uuid4()}"):
        result: dict[str, Any] = await connector_sync(str(workspace.id), str(connection_id))
    return result


async def _recorded_pages(google: FakeGoogleCalendar, clock: FixedClock) -> list[SyncPage]:
    """Account a's three pages as the connector hands them out."""
    from zoneinfo import ZoneInfo  # noqa: PLC0415

    from tumnis.modules.calendar.api import GoogleCalendar  # noqa: PLC0415
    from tumnis.modules.calendar.rules import sync_window  # noqa: PLC0415

    tz = ZoneInfo("America/New_York")
    connector = GoogleCalendar(
        api=google,
        access_token="fake-access-a",
        calendar_ids=[ACCOUNTS["a"]],
        self_email=ACCOUNTS["a"],
        window=sync_window(clock.now().astimezone(tz).date(), tz),
        clock=clock,
    )
    out: list[SyncPage] = []
    cursor: dict[str, Any] | None = None
    while True:
        page = await connector.sync(cursor)
        out.append(page)
        if not page.has_more:
            return out
        cursor = page.next_cursor


async def _new_connection(workspace: WorkspaceHandle) -> uuid.UUID:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.integrations.api import seed_connection  # noqa: PLC0415

    async with tenant_session(workspace.ctx) as s:
        return await seed_connection(s, "calendar", "google_calendar", f"x-{uuid.uuid4().hex}")


def _state(db: DbUrls, connection_id: uuid.UUID) -> set[tuple[Any, ...]]:
    return {
        (r["external_id"], r["start_at"], r["end_at"], r["busy"], r["deleted_at"] is None)
        for r in rows(db, "events", connection_id)
    }


DELIVERIES = st.lists(st.sampled_from(range(PAGE_COUNT)), max_size=4).flatmap(
    lambda extra: st.permutations([*range(PAGE_COUNT), *extra])
)


@pytest.mark.req("FR-14.3")
@pytest.mark.wp("P1-09")
@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(order=DELIVERIES)
async def test_upsert_idempotent(
    app_db: DbUrls,
    workspace: WorkspaceHandle,
    google: FakeGoogleCalendar,
    clock: FixedClock,
    order: list[int],
) -> None:
    """T-P1-09-03
    Hypothesis: account a's recorded pages delivered in any order, some more than once,
    leave the same `events` rows (external id, start, end, busy, live) as one ordered
    delivery. Each example writes to fresh connections.
    """
    from tumnis.modules.calendar.api import GoogleCalendar, ingest_events_page  # noqa: PLC0415

    pages = await _recorded_pages(google, clock)
    assert len(pages) == PAGE_COUNT
    connector = GoogleCalendar(api=google, clock=clock)

    reference = await _new_connection(workspace)
    for page in pages:
        await ingest_events_page(workspace.ctx, reference, connector, page)
    shuffled = await _new_connection(workspace)
    for index in order:
        await ingest_events_page(workspace.ctx, shuffled, connector, pages[index])

    expected = _state(app_db, reference)
    assert expected, "the recorded pages hold events"
    assert _state(app_db, shuffled) == expected
    assert len(rows(app_db, "events", shuffled)) == len(expected)


@pytest.mark.req("FR-1.3")
@pytest.mark.wp("P1-09")
async def test_cancelled_events_disappear(
    app_db: DbUrls,
    workspace: WorkspaceHandle,
    google: FakeGoogleCalendar,
    oauth_client: None,
    dbos: type[DBOS],
) -> None:
    """T-P1-09-04
    `a-kickoff` is live after sync 1; sync 2 reports it cancelled: its row is
    soft-deleted and `events_between` no longer returns it.
    """
    from tumnis.modules.calendar.api import events_between  # noqa: PLC0415

    connection = await connect(workspace.ctx, "a")
    kickoff = f"{ACCOUNTS['a']}:a-kickoff"

    await _sync(workspace, connection)
    first = await events_between(workspace.ctx, DAY_START, DAY_END)
    assert kickoff in {e.external_id for e in first}

    google.script_pages(ACCOUNTS["a"], [recording("account_a_cancelled")["response"]])
    await _sync(workspace, connection)

    (row,) = [r for r in rows(app_db, "events", connection) if r["external_id"] == kickoff]
    assert row["deleted_at"] is not None
    second = await events_between(workspace.ctx, DAY_START, DAY_END)
    assert kickoff not in {e.external_id for e in second}
    assert {e.external_id for e in second} == {e.external_id for e in first} - {kickoff}


@pytest.mark.req("FR-1.3")
@pytest.mark.wp("P1-09")
async def test_two_accounts_merge(
    app_db: DbUrls,
    workspace: WorkspaceHandle,
    google: FakeGoogleCalendar,
    oauth_client: None,
    dbos: type[DBOS],
) -> None:
    """T-P1-09-05
    Both accounts' events appear in `events_between`; the kickoff both calendars hold
    (one iCalUID) is two rows, one per connection, and both are busy.
    """
    from tumnis.modules.calendar.api import events_between  # noqa: PLC0415

    conn_a = await connect(workspace.ctx, "a")
    conn_b = await connect(workspace.ctx, "b")
    await _sync(workspace, conn_a)
    await _sync(workspace, conn_b)

    events = await events_between(workspace.ctx, DAY_START, DAY_END)
    assert {e.connection_id for e in events} == {conn_a, conn_b}
    kickoffs = [e for e in events if e.title == "Acme kickoff"]
    assert len(kickoffs) == 2
    assert {e.connection_id for e in kickoffs} == {conn_a, conn_b}
    assert all(e.busy for e in kickoffs)
    assert kickoffs[0].start_at == kickoffs[1].start_at
    assert {e.title for e in events if e.connection_id == conn_b} >= {"Client lunch"}
    # Everything returned overlaps the day asked for.
    assert all(e.start_at < DAY_END and e.end_at > DAY_START for e in events)


@pytest.mark.req("FR-14.4")
@pytest.mark.wp("P1-09")
async def test_killed_mid_page_resumes_from_cursor(  # noqa: PLR0917
    app_db: DbUrls,
    workspace: WorkspaceHandle,
    oauth_client: None,
    worker_killer: WorkerKillerFactory,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-P1-09-09
    A worker killed right after page 2 of 3 commits: the restarted worker fetches page 3
    only (the fake logs each events.list request by pageToken), and the sync finishes once
    with every page's events stored.
    """
    log = tmp_path / "google-pages.log"
    monkeypatch.setenv("CALENDAR_FAKE_PAGE_LOG", str(log))
    connection = await connect(workspace.ctx, "a", now=datetime.now(UTC))
    workflow_id = f"test-kill-{uuid.uuid4()}"

    killer = worker_killer(
        "calendar.sync.page_2.committed",
        events=0,
        imports=("tumnis.modules.calendar.tests.integration._page_log",),
    )
    code = await killer.enqueue_until_killed(
        queue_name="sync",
        workflow_name="calendar_connector_sync",
        workflow_id=workflow_id,
        args=(str(workspace.id), str(connection)),
    )
    assert code == 137
    assert log.read_text().splitlines() == ["first", "a-p2"]

    status = await killer.restart_until_done(workflow_id)
    assert status == "SUCCESS", killer.log_tail()
    assert log.read_text().splitlines() == ["first", "a-p2", "a-p3"]
    stored = {r["external_id"] for r in rows(app_db, "events", connection)}
    for page_name in ("account_a_page1", "account_a_page2", "account_a_page3"):
        for item in recording(page_name)["response"]["items"]:
            if item["status"] != "cancelled":
                assert f"{ACCOUNTS['a']}:{item['id']}" in stored
    assert len(outbox(app_db, "calendar.synced")) == 1


@pytest.mark.req("FR-14.4")
@pytest.mark.wp("P1-09")
async def test_revoked_grant_marks_needs_reauth(  # noqa: PLR0917
    app_db: DbUrls,
    workspace: WorkspaceHandle,
    google: FakeGoogleCalendar,
    oauth_client: None,
    clock: FixedClock,
    dbos: type[DBOS],
) -> None:
    """T-P1-09-10
    Both access tokens have expired. Refreshing account a's answers the recorded
    `invalid_grant`: a is `needs_reauth` in the account list (Settings) and its connector
    reports degraded health; account b refreshes and syncs its events.
    """
    from tumnis.modules.calendar.api import (  # noqa: PLC0415
        build_connector,
        events_between,
        list_accounts,
    )

    conn_a = await connect(workspace.ctx, "a", now=T0 - timedelta(hours=2))
    conn_b = await connect(workspace.ctx, "b", now=T0 - timedelta(hours=2))
    google.revoke(refresh_token("a"))

    result_a = await _sync(workspace, conn_a)
    result_b = await _sync(workspace, conn_b)

    assert result_a["status"] == "needs_reauth"
    assert result_b["status"] == "synced"
    accounts = {acct.connection_id: acct for acct in await list_accounts(workspace.ctx)}
    assert accounts[conn_a].status == "needs_reauth"
    assert accounts[conn_b].status == "connected"
    assert accounts[conn_b].last_sync_at == clock.now()
    health_a = await (await build_connector(workspace.ctx, conn_a, api=google)).health()
    health_b = await (await build_connector(workspace.ctx, conn_b, api=google)).health()
    assert (health_a, health_b) == ("degraded", "ok")
    events = await events_between(workspace.ctx, DAY_START, WEEK_END)
    assert {e.connection_id for e in events} == {conn_b}
    assert rows(app_db, "events", conn_a) == []
    assert google.refreshed == [refresh_token("a"), refresh_token("b")]


@pytest.mark.req("FR-1.3")
@pytest.mark.wp("P1-09")
async def test_calendar_synced_emitted_once_per_sync(
    app_db: DbUrls,
    workspace: WorkspaceHandle,
    google: FakeGoogleCalendar,
    oauth_client: None,
    dbos: type[DBOS],
) -> None:
    """T-P1-09-11
    Each completed sync emits one `calendar.synced {connection_id, window}`; a sync that
    fails (Google unavailable on every attempt) emits none.
    """
    from tumnis.core.adapters.errors import AdapterUnavailable  # noqa: PLC0415

    conn_a = await connect(workspace.ctx, "a")
    await _sync(workspace, conn_a)
    first = outbox(app_db, "calendar.synced")
    assert len(first) == 1
    payload = first[0]["payload"]
    assert payload["connection_id"] == str(conn_a)
    assert set(payload["window"]) == {"start", "end"}
    assert payload["window"]["start"] < payload["window"]["end"]

    await _sync(workspace, conn_a)
    assert len(outbox(app_db, "calendar.synced")) == 2

    conn_b = await connect(workspace.ctx, "b")
    google.fail_events(ACCOUNTS["b"], AdapterUnavailable("calendar.google", "list_events", "503"))
    with pytest.raises(Exception):  # noqa: B017, PT011  # DBOS may wrap the adapter error
        await _sync(workspace, conn_b)
    synced = outbox(app_db, "calendar.synced")
    assert len(synced) == 2
    assert {row["payload"]["connection_id"] for row in synced} == {str(conn_a)}


@pytest.mark.req("FR-14.3")
@pytest.mark.wp("P1-09")
async def test_one_sync_per_account_at_a_time(
    app_db: DbUrls,
    workspace: WorkspaceHandle,
    google: FakeGoogleCalendar,
    oauth_client: None,
    dbos: type[DBOS],
) -> None:
    """A second sync of an account (Sync now during the scheduled one) ends `busy` while
    the first holds the account, touching neither its cursor nor its events; the first
    finishes with one `calendar.synced`, and the next sync runs normally."""
    import asyncio  # noqa: PLC0415

    conn_a = await connect(workspace.ctx, "a")
    hold = google.hold_events(ACCOUNTS["a"])
    first = asyncio.create_task(_sync(workspace, conn_a))
    await asyncio.wait_for(hold.entered.wait(), 10)

    second = await _sync(workspace, conn_a)
    assert second == {"status": "busy"}
    assert rows(app_db, "events", conn_a) == []

    hold.release.set()
    assert (await first)["status"] == "synced"
    events = rows(app_db, "events", conn_a)
    assert events
    assert len(outbox(app_db, "calendar.synced")) == 1

    assert (await _sync(workspace, conn_a))["status"] == "synced"
    assert len(outbox(app_db, "calendar.synced")) == 2


@pytest.mark.req("FR-14.3")
@pytest.mark.wp("P1-09")
async def test_failed_sync_frees_the_account(
    app_db: DbUrls,
    workspace: WorkspaceHandle,
    google: FakeGoogleCalendar,
    oauth_client: None,
    dbos: type[DBOS],
) -> None:
    """A sync that failed for good no longer holds its account: the next sync runs and
    emits `calendar.synced`."""
    from tumnis.core.adapters.errors import AdapterUnavailable  # noqa: PLC0415

    conn_a = await connect(workspace.ctx, "a")
    google.fail_events(ACCOUNTS["a"], AdapterUnavailable("calendar.google", "list_events", "503"))
    with pytest.raises(Exception):  # noqa: B017, PT011  # DBOS may wrap the adapter error
        await _sync(workspace, conn_a)
    assert _holder(app_db, conn_a) is not None

    google.fail_events(ACCOUNTS["a"], None)
    assert (await _sync(workspace, conn_a))["status"] == "synced"
    assert len(outbox(app_db, "calendar.synced")) == 1
    assert _holder(app_db, conn_a) is None


def _holder(db: DbUrls, connection_id: uuid.UUID) -> str | None:
    """The workflow holding the account's sync lease, if any."""
    holder: str | None = scalar(
        db, "SELECT sync_owner FROM calendar_accounts WHERE connection_id = %s", connection_id
    )
    return holder


TEAM = "team@group.calendar.example.com"


def _team_page() -> dict[str, Any]:
    """One events.list page of the shared team calendar: account a's last recorded page
    with its event ids renamed, so they never collide with the primary calendar's."""
    response = recording("account_a_page3")["response"]
    for item in response["items"]:
        item["id"] = f"team-{item['id']}"
    return response


@pytest.mark.req("FR-14.4")
@pytest.mark.wp("P1-09")
@pytest.mark.xfail(strict=True, reason="review:P1-09 reconnect prunes the selection")
async def test_reconnect_drops_calendars_no_longer_listed(
    app_db: DbUrls,
    workspace: WorkspaceHandle,
    google: FakeGoogleCalendar,
    oauth_client: None,
    dbos: type[DBOS],
) -> None:
    """A reconnect keeps the selection only within the calendars Google still lists: a
    chosen calendar that is gone (unshared or deleted) leaves the selection, its events
    are soft-deleted and the next sync succeeds."""
    from tumnis.modules.calendar.adapters.port import CalendarInfo  # noqa: PLC0415
    from tumnis.modules.calendar.api import list_accounts, select_calendars  # noqa: PLC0415

    team = CalendarInfo(id=TEAM, summary="Team", primary=False, time_zone="America/New_York")
    conn = await connect(workspace.ctx, "a", also_listed=[team])
    (account,) = await list_accounts(workspace.ctx)
    await select_calendars(
        workspace.ctx, account.id, [ACCOUNTS["a"], TEAM], expected_version=account.version
    )
    google.script_pages(TEAM, [_team_page()])
    assert (await _sync(workspace, conn))["status"] == "synced"
    assert any(r["calendar_id"] == TEAM for r in rows(app_db, "events", conn))

    await connect(workspace.ctx, "a")  # the team calendar is no longer listed

    (account,) = await list_accounts(workspace.ctx)
    assert account.selected_calendar_ids == [ACCOUNTS["a"]]
    for row in rows(app_db, "events", conn):
        assert (row["deleted_at"] is not None) == (row["calendar_id"] == TEAM)
    assert (await _sync(workspace, conn))["status"] == "synced"
