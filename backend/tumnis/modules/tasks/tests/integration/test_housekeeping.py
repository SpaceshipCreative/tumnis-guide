"""The scheduled workflows against Postgres and DBOS (P0-19, FR-3.6, REL-2, REL-3, REL-6).

- `day_close_tick` (every 5 minutes) closes each workspace's local day once its local
  midnight passes: Today tasks go back to Backlog (`rollover_count` + 1, one
  `task.status_changed` each with actor `system`) and one `day_closes` row is written. It
  survives a worker killed inside `roll_over_today`, and follows timezone changes.
- `housekeeping` (hourly at :17) purges tasks trashed more than 30 days ago and deletes
  expired idempotency keys, in batches.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from tests._pg import OWNER
from tumnis.modules.tasks.tests.conftest import drain_workflows, outbox, owner_rows

if TYPE_CHECKING:
    from dbos import DBOS
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkerKillerFactory, WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.tasks.api import TaskOut
    from tumnis.modules.tasks.tests.conftest import (
        Actors,
        MakeTask,
        SetStatus,
        TzWorkspace,
    )

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def _owner_exec(db: DbUrls, query: str, params: tuple[Any, ...] = ()) -> None:
    import psycopg  # noqa: PLC0415

    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        conn.execute(query.encode(), params)


async def _tick(clock: FixedClock) -> None:
    from tumnis.modules.tasks.workflows import day_close_tick  # noqa: PLC0415

    await day_close_tick(clock.now(), None)
    await drain_workflows()


def _state(db: DbUrls, tasks: list[TaskOut]) -> list[tuple[str, int]]:
    found = owner_rows(
        db,
        "SELECT id, status::text, rollover_count FROM tasks WHERE id = ANY(%s)",
        ([t.id for t in tasks],),
    )
    rows = {row[0]: (row[1], row[2]) for row in found}
    return [rows[t.id] for t in tasks]


def _system_rollovers(db: DbUrls) -> list[dict[str, Any]]:
    return [p for p in outbox(db, "task.status_changed") if p["actor"] == "system"]


def _day_closes(db: DbUrls) -> list[tuple[Any, ...]]:
    return owner_rows(db, "SELECT day, rolled_over FROM day_closes ORDER BY day")


async def _today_tasks(make_task: MakeTask, set_status: SetStatus, n: int) -> list[TaskOut]:
    made = [await make_task(label="human", estimate_minutes=15) for _ in range(n)]
    return [await set_status(task, "today") for task in made]


@pytest.mark.req("FR-3.6")
@pytest.mark.wp("P0-19")
async def test_day_close_returns_today_tasks_and_increments(  # noqa: PLR0917
    dbos: type[DBOS],
    workspace: WorkspaceHandle,
    clock: FixedClock,
    make_task: MakeTask,
    set_status: SetStatus,
    tz_workspace: TzWorkspace,
    db: DbUrls,
) -> None:
    """T-P0-19-13
    Given a New York workspace last anchored at 2026-03-08T05:00Z and 3 Today tasks, one
    with `rollover_count=2`, when the clock passes 2026-03-09T04:00Z (local midnight after
    the 23-hour day) and `day_close_tick` runs, then all 3 are Backlog with counts 1, 1, 3,
    one `task.status_changed` per task with actor `system`, and one `day_closes` row for
    2026-03-08. A tick at 03:55Z before it and a second tick after it change nothing.
    """
    await tz_workspace("America/New_York", at=_at("2026-03-08T05:00Z"))
    clock.set(_at("2026-03-09T03:55Z"))
    tasks = await _today_tasks(make_task, set_status, 3)
    _owner_exec(db, "UPDATE tasks SET rollover_count = 2 WHERE id = %s", (tasks[2].id,))

    await _tick(clock)
    assert _state(db, tasks) == [("today", 0), ("today", 0), ("today", 2)]
    assert _day_closes(db) == []

    clock.set(_at("2026-03-09T04:00Z"))
    await _tick(clock)
    assert _state(db, tasks) == [("backlog", 1), ("backlog", 1), ("backlog", 3)]
    rolled = _system_rollovers(db)
    assert sorted(p["task_id"] for p in rolled) == sorted(str(t.id) for t in tasks)
    assert {(p["from"], p["to"]) for p in rolled} == {("today", "backlog")}
    assert _day_closes(db) == [(date(2026, 3, 8), 3)]

    clock.advance(minutes=5)
    await _tick(clock)
    assert _state(db, tasks) == [("backlog", 1), ("backlog", 1), ("backlog", 3)]
    assert len(_system_rollovers(db)) == 3
    assert _day_closes(db) == [(date(2026, 3, 8), 3)]


@pytest.mark.req("FR-3.6")
@pytest.mark.wp("P0-19")
async def test_day_close_leaves_other_statuses(  # noqa: PLR0917
    dbos: type[DBOS],
    workspace: WorkspaceHandle,
    clock: FixedClock,
    make_task: MakeTask,
    set_status: SetStatus,
    tz_workspace: TzWorkspace,
    db: DbUrls,
) -> None:
    """T-P0-19-14
    In progress, waiting on human, in review, backlog and done tasks stay where they are
    through a day close; only the Today task rolls over.
    """
    await tz_workspace("America/New_York", at=_at("2026-03-08T05:00Z"))
    clock.set(_at("2026-03-09T03:00Z"))
    [today] = await _today_tasks(make_task, set_status, 1)

    async def human_task() -> TaskOut:
        return await make_task(label="human", estimate_minutes=15)

    backlog = await human_task()
    in_progress = await set_status(await human_task(), "in_progress")
    waiting = await set_status(
        await set_status(await human_task(), "in_progress"), "waiting_on_human", "agent"
    )
    review = await set_status(
        await set_status(await human_task(), "in_progress"), "in_review", "agent"
    )
    done = await set_status(await set_status(await human_task(), "in_progress"), "done")
    others = [backlog, in_progress, waiting, review, done]
    before = _state(db, others)

    clock.set(_at("2026-03-09T04:05Z"))
    await _tick(clock)
    assert _state(db, [today]) == [("backlog", 1)]
    assert _state(db, others) == before
    assert [s for s, _ in before] == [
        "backlog",
        "in_progress",
        "waiting_on_human",
        "in_review",
        "done",
    ]
    assert [p["task_id"] for p in _system_rollovers(db)] == [str(today.id)]
    assert _day_closes(db) == [(date(2026, 3, 8), 1)]


@pytest.mark.req("FR-3.6", "REL-3")
@pytest.mark.wp("P0-19")
async def test_day_close_resumes_after_worker_kill(  # noqa: PLR0917
    worker_killer: WorkerKillerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    make_task: MakeTask,
    set_status: SetStatus,
    db: DbUrls,
) -> None:
    """T-P0-19-15
    A worker running `close_day` is killed inside `roll_over_today` after the UPDATE and
    before the commit; the restarted worker reruns the step and the workflow finishes once:
    counts rise by one, one `day_closes` row, one `task.status_changed` per task.
    """
    killer = worker_killer("tasks.roll_over_today")
    tasks = await _today_tasks(make_task, set_status, 3)
    now = _at("2026-03-09T04:00Z")
    workflow_id = f"day_close:{workspace.id}:2026-03-08"

    proc = await killer.start_worker(armed=True)
    client = killer.dbos_client()
    await _enqueue_until_accepted(
        client,
        {"queue_name": "maintenance", "workflow_name": "close_day", "workflow_id": workflow_id},
        workspace.id,
        date(2026, 3, 8),
        now,
    )
    code = await asyncio.wait_for(proc.wait(), 30)
    assert code == 137, killer.log_tail()
    assert _state(db, tasks) == [("today", 0)] * 3  # the killed step's transaction rolled back

    proc = await killer.start_worker(armed=False)
    try:
        result: object = await asyncio.wait_for(
            asyncio.to_thread(client.retrieve_workflow(workflow_id).get_result), 30
        )
    finally:
        await killer.stop_worker(proc)
    assert result == 3
    assert _state(db, tasks) == [("backlog", 1)] * 3
    assert _day_closes(db) == [(date(2026, 3, 8), 3)]
    assert len(_system_rollovers(db)) == 3


async def _enqueue_until_accepted(client: Any, options: dict[str, Any], *args: Any) -> None:
    """The worker creates the DBOS system tables and queues at launch; retry until then."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 30
    while True:
        try:
            await client.enqueue_async(options, *args)
        except Exception:
            if loop.time() > deadline:
                raise
            await asyncio.sleep(0.2)
        else:
            return


@pytest.mark.req("FR-3.6", "REL-6")
@pytest.mark.wp("P0-19")
async def test_changing_timezone_moves_next_day_close(  # noqa: PLR0917
    dbos: type[DBOS],
    app: FastAPI,
    session_client: SessionClient,
    clock: FixedClock,
    make_task: MakeTask,
    set_status: SetStatus,
    db: DbUrls,
) -> None:
    """T-P0-19-16
    A New York workspace switches to Australia/Sydney through `PUT /v1/settings/workspace`
    at 2026-03-08T12:00Z (08:00 EDT, 23:00 AEDT). Ticks then close the day at Sydney
    midnight (2026-03-08T13:00Z) and not at the old New York midnight (2026-03-09T04:00Z);
    the next close is the next Sydney midnight (2026-03-09T13:00Z).
    """
    clock.set(_at("2026-03-08T12:00Z"))
    current = (await session_client.get("/v1/settings/workspace")).json()
    put = await session_client.put(
        "/v1/settings/workspace",
        json={"timezone": "Australia/Sydney", "version": current["version"]},
    )
    assert put.status_code == 200, put.text
    [first] = await _today_tasks(make_task, set_status, 1)

    clock.set(_at("2026-03-08T12:55Z"))
    await _tick(clock)
    assert _state(db, [first]) == [("today", 0)]

    clock.set(_at("2026-03-08T13:00Z"))
    await _tick(clock)
    assert _state(db, [first]) == [("backlog", 1)]
    assert _day_closes(db) == [(date(2026, 3, 8), 1)]

    [second] = await _today_tasks(make_task, set_status, 1)
    clock.set(_at("2026-03-09T04:00Z"))  # New York midnight: nothing
    await _tick(clock)
    assert _state(db, [second]) == [("today", 0)]

    clock.set(_at("2026-03-09T13:00Z"))  # the next Sydney midnight
    await _tick(clock)
    assert _state(db, [second]) == [("backlog", 1)]
    assert _day_closes(db) == [(date(2026, 3, 8), 1), (date(2026, 3, 9), 1)]


@pytest.mark.req("REL-6")
@pytest.mark.wp("P0-19")
async def test_trash_purge_after_retention(  # noqa: PLR0917
    dbos: type[DBOS],
    workspace: WorkspaceHandle,
    clock: FixedClock,
    actors: Actors,
    make_task: MakeTask,
    db: DbUrls,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-P0-19-17
    `housekeeping` hard-deletes tasks trashed more than 30 days ago, with their comments and
    subtasks trashed alongside, in batches (2 here) until none is left; tasks trashed 29
    days ago and live tasks stay.
    """
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.modules.tasks import api, workflows  # noqa: PLC0415

    monkeypatch.setattr(workflows, "HOUSEKEEPING_BATCH", 2)
    now = clock.now()
    old = [await make_task(title=f"Old {i}") for i in range(4)]
    old_child = await make_task(
        title="Old child", project_id=old[0].project_id, parent_id=old[0].id
    )
    recent = await make_task(title="Recent")
    live = await make_task(title="Live")
    async with tenant_session(WorkspaceContext(workspace.id, actors.human)) as s:
        await api.add_comment(s, actors.human, old[0].id, "gone with it", now=now)
        await api.add_comment(s, actors.human, live.id, "stays", now=now)
    for task in [*old, old_child]:
        _owner_exec(
            db,
            "UPDATE tasks SET deleted_at = %s WHERE id = %s",
            (now - timedelta(days=31), task.id),
        )
    _owner_exec(
        db, "UPDATE tasks SET deleted_at = %s WHERE id = %s", (now - timedelta(days=29), recent.id)
    )

    from tumnis.modules.tasks.workflows import housekeeping  # noqa: PLC0415

    await housekeeping(now, None)

    left = {row[0] for row in owner_rows(db, "SELECT title FROM tasks")}
    assert left == {"Recent", "Live"}
    comments = [row[0] for row in owner_rows(db, "SELECT body_md FROM task_comments")]
    assert comments == ["stays"]


@pytest.mark.req("REL-2")
@pytest.mark.wp("P0-19")
async def test_idempotency_keys_expire(
    dbos: type[DBOS], workspace: WorkspaceHandle, clock: FixedClock, db: DbUrls
) -> None:
    """T-P0-19-18
    `housekeeping` deletes idempotency keys whose `expires_at` is in the past, in every
    workspace, and keeps the ones still valid.
    """
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.modules.tasks.workflows import housekeeping  # noqa: PLC0415

    other = make_workspace(db, "Other")
    now = clock.now()
    for ws, key, expires in [
        (workspace.id, "expired", now - timedelta(minutes=1)),
        (workspace.id, "valid", now + timedelta(hours=1)),
        (other, "expired-other", now - timedelta(hours=2)),
    ]:
        _owner_exec(
            db,
            "INSERT INTO idempotency_keys (workspace_id, principal, key, route, method,"
            " request_hash, expires_at) VALUES (%s, %s, %s, '/v1/tasks', 'POST', %s, %s)",
            (ws, f"user:{uuid.uuid4()}", key, b"hash", expires),
        )

    await housekeeping(now, None)

    assert [row[0] for row in owner_rows(db, "SELECT key FROM idempotency_keys")] == ["valid"]
