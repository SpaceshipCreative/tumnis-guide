"""The sync framework (P3-02, FR-14.5, REL-3): `connector_sync` saves its cursor with each
page and resumes after a kill, per-provider rate limits hold, a failing sync shows its
status, a review item and its age, and the tick enqueues each due connection once."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.integrations.tests.integration._connections import (
    advancing_sleep,
    metrics_app,
    ready,
    rows,
    run_sync,
    scrape,
    wired,
)

if TYPE_CHECKING:
    from pathlib import Path

    from dbos import DBOS, DBOSClient

    from tests._pg import DbUrls
    from tests.fixtures import MasterKeyFile, WorkerKillerFactory, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("REL-3", "FR-14.5")
@pytest.mark.wp("P3-02")
async def test_cursor_saved_per_page_and_resumes_after_kill(  # noqa: PLR0917
    app_db: DbUrls,
    workspace: WorkspaceHandle,
    master_key_file: MasterKeyFile,
    worker_killer: WorkerKillerFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-P3-02-05
    The fake source has four pages. A worker killed inside page 3's persist step, before
    its commit, leaves pages 1 and 2 stored and the saved cursor at page 3's input. The
    restarted worker fetches page 3 again, then page 4, and the sync finishes once: every
    message is stored exactly once (version 1), with one `items.ingested` per page.
    """
    log = tmp_path / "fake-pages.log"
    monkeypatch.setenv("INTEGRATIONS_FAKE_PAGE_LOG", str(log))
    connection_id = await ready(workspace.ctx)
    workflow_id = f"test-kill-{uuid.uuid4()}"

    killer = worker_killer(
        "integrations.sync.page_3.persisting",
        events=0,
        imports=("tumnis.modules.integrations.tests.integration._fake_pages",),
    )
    code = await killer.enqueue_until_killed(
        queue_name="sync",
        workflow_name="integrations_connector_sync",
        workflow_id=workflow_id,
        args=(str(workspace.id), str(connection_id)),
    )
    assert code == 137, killer.log_tail()
    assert log.read_text().splitlines() == ["1", "2", "3"]
    stored = {r["external_id"] for r in rows(app_db, "SELECT external_id FROM messages")}
    assert stored == {"msg-p1-1", "msg-p1-2", "msg-p2-1", "msg-p2-2"}
    (state,) = rows(
        app_db,
        "SELECT scope, cursor, page_no FROM sync_state WHERE connection_id = %s",
        connection_id,
    )
    assert state["scope"] == "default"
    assert state["cursor"]["page"] == 2  # page 3's input cursor
    assert state["page_no"] == 2

    status = await killer.restart_until_done(workflow_id)
    assert status == "SUCCESS", killer.log_tail()
    assert log.read_text().splitlines() == ["1", "2", "3", "3", "4"]
    messages = rows(app_db, "SELECT external_id, version FROM messages ORDER BY external_id")
    assert [m["external_id"] for m in messages] == [
        f"msg-p{page}-{n}" for page in range(1, 5) for n in (1, 2)
    ]
    assert {m["version"] for m in messages} == {1}
    ingested = rows(app_db, "SELECT payload FROM outbox WHERE name = 'items.ingested'")
    assert len(ingested) == 4
    assert {p["payload"]["connection_id"] for p in ingested} == {str(connection_id)}
    (conn,) = rows(
        app_db, "SELECT status, last_success_at FROM connections WHERE id = %s", connection_id
    )
    assert conn["status"] == "ok"
    assert conn["last_success_at"] is not None


@pytest.mark.req("FR-14.5")
@pytest.mark.wp("P3-02")
async def test_provider_rate_limit_holds(
    app_db: DbUrls,
    workspace: WorkspaceHandle,
    master_key_file: MasterKeyFile,
    clock: FixedClock,
) -> None:
    """T-P3-02-07
    With `PROVIDER_LIMITS["fake"]` at 10 requests per 60 seconds and a fixed clock, 30
    fetches requested at once for one account all run, none fails, and no 60-second
    window holds more than 10 starts: the rest wait (the sleep moves the clock).
    """
    from tumnis.modules.integrations.adapters.fake_source import FakeSource  # noqa: PLC0415
    from tumnis.modules.integrations.api import fetch_page  # noqa: PLC0415
    from tumnis.modules.integrations.rules import PROVIDER_LIMITS, ProviderLimit  # noqa: PLC0415

    assert PROVIDER_LIMITS["fake"] == ProviderLimit(requests=10, period_s=60)
    connection_id = await ready(workspace.ctx)
    source = FakeSource(clock=clock)
    sleep = advancing_sleep(clock)

    pages = await asyncio.gather(
        *(
            fetch_page(
                workspace.ctx, connection_id, None, connector=source, clock=clock, sleep=sleep
            )
            for _ in range(30)
        )
    )

    assert len(pages) == 30
    starts = sorted(source.started)
    assert len(starts) == 30
    window = timedelta(seconds=60)
    busiest = max(sum(1 for t in starts if start <= t < start + window) for start in starts)
    assert busiest <= 10
    assert busiest == 10  # it waits only as long as it must


@pytest.mark.req("REL-3")
@pytest.mark.wp("P3-02")
async def test_failing_sync_shows_status_and_metric(  # noqa: PLR0917
    app_db: DbUrls,
    workspace: WorkspaceHandle,
    master_key_file: MasterKeyFile,
    dbos: type[DBOS],
    dbos_sys_db: DbUrls,
    clock: FixedClock,
    tmp_path: Path,
) -> None:
    """T-P3-02-09
    A connection syncs once, then its provider answers HTTP 500 three times and 401 once.
    After each 500 the connection is `degraded` with "Server error from provider,
    retrying" and its next sync backs off; after the 401 it is `auth_required` with "Sign
    in again" and one `connection_auth` review item exists however often the event is
    delivered. The sync age on /metrics is above the provider's cadence and grows.
    """
    from tests.fixtures import make_envelope  # noqa: PLC0415
    from tumnis.core.adapters.errors import AdapterUnavailable  # noqa: PLC0415
    from tumnis.core.events import get_subscriber  # noqa: PLC0415
    from tumnis.modules.integrations.adapters.fake_source import FakeSource  # noqa: PLC0415
    from tumnis.modules.integrations.api import ReauthRequired, get_connection  # noqa: PLC0415
    from tumnis.modules.integrations.rules import DEFAULT_SYNC_MIN  # noqa: PLC0415

    connection_id = await ready(workspace.ctx)
    source = FakeSource(clock=clock)
    with wired(clock, sources={"fake": source}):
        assert (await run_sync(workspace.id, connection_id))["status"] == "ok"
        synced = await get_connection(workspace.ctx, connection_id)
        assert synced.last_success_at == clock.now()

        source.script_errors(
            *(
                AdapterUnavailable("integrations.connector.fake", "sync", "HTTP 500")
                for _ in range(3)
            ),
            ReauthRequired("fake", "HTTP 401"),
        )
        backoff: list[Any] = []
        for _ in range(3):
            clock.advance(timedelta(minutes=1))
            await run_sync(workspace.id, connection_id)
            seen = await get_connection(workspace.ctx, connection_id)
            assert seen.status == "degraded"
            assert seen.status_detail == "Server error from provider, retrying"
            assert seen.next_sync_at is not None
            backoff.append(seen.next_sync_at - clock.now())
        assert backoff == sorted(backoff)
        assert backoff[0] < backoff[-1]

        await run_sync(workspace.id, connection_id)
        failed = await get_connection(workspace.ctx, connection_id)
    assert failed.status == "auth_required"
    assert failed.status_detail == "Sign in again"
    assert failed.last_success_at == synced.last_success_at

    events = rows(app_db, "SELECT * FROM outbox WHERE name = 'connection.auth_required'")
    assert len(events) == 1
    deliver = get_subscriber("tasks.connection_auth_review").handler
    for _ in range(2):
        await deliver(
            make_envelope(events[0]["name"], events[0]["payload"], workspace, events[0]["event_id"])
        )
    items = rows(
        app_db,
        "SELECT kind, target_type, target_id, dedupe_key FROM review_items "
        "WHERE kind = 'connection_auth' AND decided_at IS NULL",
    )
    assert len(items) == 1
    assert items[0]["target_id"] == connection_id
    assert items[0]["dedupe_key"] == f"conn_auth:{connection_id}"

    cadence_s = DEFAULT_SYNC_MIN["fake"] * 60
    async with metrics_app(app_db, dbos_sys_db, clock, tmp_path) as client:
        first = await scrape(client, "tumnis_connector_sync_age_seconds")
        await asyncio.sleep(1.1)
        second = await scrape(client, "tumnis_connector_sync_age_seconds")
    key = (("provider", "fake"),)
    assert first[key] > cadence_s
    assert second[key] > first[key]


@pytest.mark.req("FR-14.5")
@pytest.mark.wp("P3-02")
async def test_tick_enqueues_each_due_connection_once(  # noqa: PLR0917
    app_db: DbUrls,
    workspace: WorkspaceHandle,
    master_key_file: MasterKeyFile,
    dbos: type[DBOS],
    dbos_client: DBOSClient,
    clock: FixedClock,
) -> None:
    """T-P3-02-10
    Two due connections, one not yet due and one waiting for a new sign-in. Two ticks in
    the same minute enqueue exactly one `connector_sync` per due connection (the
    deduplication ID `sync:<id>`) and none for the other two.
    """
    from dbos import SetWorkflowID  # noqa: PLC0415

    from tumnis.modules.integrations.adapters.fake_source import FakeSource  # noqa: PLC0415
    from tumnis.modules.integrations.workflows import connector_sync_tick  # noqa: PLC0415

    due_a = await ready(workspace.ctx, label="A")
    due_b = await ready(workspace.ctx, label="B")
    await ready(workspace.ctx, label="C", next_sync_at=clock.now() + timedelta(minutes=30))
    await ready(workspace.ctx, label="D", status="auth_required")

    with wired(clock, sources={"fake": FakeSource(clock=clock)}):
        minute = clock.now().replace(second=0, microsecond=0)
        for offset in (0, 20):
            with SetWorkflowID(f"test-tick-{uuid.uuid4()}"):
                await connector_sync_tick(minute + timedelta(seconds=offset), None)
        await asyncio.sleep(0.5)  # let a second enqueue race the first sync

        loop = asyncio.get_running_loop()
        deadline = loop.time() + 30
        while True:
            flows = await asyncio.to_thread(
                dbos_client.list_workflows, name="integrations_connector_sync"
            )
            if (
                all(f.status not in {"PENDING", "ENQUEUED"} for f in flows)
                or loop.time() > deadline
            ):
                break
            await asyncio.sleep(0.1)

    synced = sorted(f.input["args"][1] for f in flows if f.input is not None)
    assert synced == sorted([str(due_a), str(due_b)])
    assert all(f.status == "SUCCESS" for f in flows)
