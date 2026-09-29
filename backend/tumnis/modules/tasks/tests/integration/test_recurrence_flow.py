"""Recurring tasks against Postgres (P0-19, FR-3.5): the recurrence REST round trip, and one
successor per occurrence when completion and the recurrence tick race (the unique index on
`(workspace_id, recurrence_rule_id, occurrence_on)` holds under concurrency)."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.tasks.tests.conftest import outbox, owner_rows

if TYPE_CHECKING:
    from dbos import DBOS
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.tasks.tests.conftest import Actors, MakeTask, SetStatus

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _instances(db: DbUrls, rule_id: Any) -> list[tuple[Any, ...]]:
    return owner_rows(
        db,
        "SELECT occurrence_on, due_on, status::text, source, title FROM tasks"
        " WHERE recurrence_rule_id = %s ORDER BY occurrence_on",
        (rule_id,),
    )


@pytest.mark.req("FR-3.5")
@pytest.mark.wp("P0-19")
@pytest.mark.xfail(strict=True, reason="spec:P0-19")
async def test_done_and_tick_race_creates_one_instance(  # noqa: PLR0917
    dbos: type[DBOS],
    workspace: WorkspaceHandle,
    clock: FixedClock,
    actors: Actors,
    make_task: MakeTask,
    set_status: SetStatus,
    db: DbUrls,
) -> None:
    """T-P0-19-10
    Given a weekly Monday 09:00 rule whose latest instance (due Mon 2026-03-02) is overdue
    and in progress, when `change_status(done)` and `recurrence_tick` run concurrently at
    2026-03-09T12:00Z, then exactly one successor exists (due Mon 2026-03-09, a Backlog copy
    of the template with source `recurrence`), one `task.created` for it, and neither side
    raised: the loser found the successor or hit the unique index and returned.
    """
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.modules.tasks import api  # noqa: PLC0415
    from tumnis.modules.tasks.workflows import recurrence_tick  # noqa: PLC0415

    task = await make_task(title="Weekly report", label="human", estimate_minutes=30)
    ctx = WorkspaceContext(workspace.id, actors.human)
    async with tenant_session(ctx) as s:
        rec = await api.put_recurrence(
            s,
            actors.human,
            task.id,
            api.RecurrenceIn(preset="weekly", weekday=0, version=task.version),
            now=datetime(2026, 3, 1, 12, tzinfo=UTC),
        )
    assert rec.latest_occurrence_on == date(2026, 3, 2)
    started = await set_status(await _task(ctx, task.id), "in_progress")
    clock.set(datetime(2026, 3, 9, 12, tzinfo=UTC))  # Monday 08:00 EDT: the instance is overdue

    async def complete() -> None:
        async with tenant_session(ctx) as s:
            await api.change_status(
                s, actors.human, task.id, api.Status.DONE, started.version, now=clock.now()
            )

    results = await asyncio.gather(
        complete(), recurrence_tick(workspace.id, clock.now()), return_exceptions=True
    )
    assert not [r for r in results if isinstance(r, BaseException)], results

    rows = _instances(db, rec.id)
    assert rows == [
        (date(2026, 3, 2), date(2026, 3, 2), "done", "user", "Weekly report"),
        (date(2026, 3, 9), date(2026, 3, 9), "backlog", "recurrence", "Weekly report"),
    ]
    created = [p for p in outbox(db, "task.created") if p["source"] == "recurrence"]
    assert len(created) == 1

    # a second tick at the same time makes nothing: the new instance is not due yet
    await recurrence_tick(workspace.id, clock.now())
    assert len(_instances(db, rec.id)) == 2


async def _task(ctx: Any, task_id: Any) -> Any:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.tasks import api  # noqa: PLC0415

    async with tenant_session(ctx) as s:
        return await api.get_task(s, task_id)


@pytest.mark.req("FR-3.5")
@pytest.mark.wp("P0-19")
@pytest.mark.xfail(strict=True, reason="spec:P0-19")
async def test_recurrence_api_round_trip(  # noqa: PLR0915
    app: FastAPI,
    session_client: SessionClient,
    make_task: MakeTask,
    clock: FixedClock,
) -> None:
    """T-P0-19-19
    `PUT /v1/tasks/{id}/recurrence` makes the task the first instance (weekly on Friday at
    16:00 from Monday 2026-03-09: due Fri 2026-03-13, next 2026-03-20) and answers the rule
    with the task's new version; `GET` returns it and `GET /v1/recurrence?project_id=` lists
    it. Changing the rule keeps the task's due date as the first instance's day. A stale
    version is 409 `stale_version`, an invalid spec 422 `invalid_recurrence`,
    an unknown task 404. `DELETE ?version=` stops the recurrence; `GET` is then 404.
    """
    task = await make_task(title="Friday invoice run", label="human", estimate_minutes=20)
    url = f"/v1/tasks/{task.id}/recurrence"

    missing = await session_client.get(url)
    assert missing.status_code == 404, missing.text
    assert missing.json()["code"] == "not_found"

    put = await session_client.put(
        url, json={"preset": "weekly", "weekday": 4, "due_time": "16:00", "version": task.version}
    )
    assert put.status_code == 200, put.text
    rule = put.json()
    assert rule["task_id"] == str(task.id)
    assert rule["project_id"] == str(task.project_id)
    assert (rule["preset"], rule["cron"], rule["weekday"], rule["month_day"]) == (
        "weekly",
        None,
        4,
        None,
    )
    assert rule["due_time"] == "16:00:00"
    assert rule["title"] == "Friday invoice run"
    assert rule["latest_task_id"] == str(task.id)
    assert rule["latest_occurrence_on"] == "2026-03-13"
    assert rule["next_due_at"] == "2026-03-20T20:00:00Z"
    assert rule["version"] > task.version

    got_task = (await session_client.get(f"/v1/tasks/{task.id}")).json()
    assert got_task["due_on"] == "2026-03-13"
    assert got_task["version"] == rule["version"]

    got = await session_client.get(url)
    assert got.status_code == 200, got.text
    assert got.json() == rule

    listed = await session_client.get("/v1/recurrence", params={"project_id": str(task.project_id)})
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [rule["id"]]

    stale = await session_client.put(url, json={"preset": "daily", "version": task.version})
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "stale_version"

    invalid = await session_client.put(url, json={"preset": "weekly", "version": rule["version"]})
    assert invalid.status_code == 422, invalid.text
    assert invalid.json()["code"] == "invalid_recurrence"
    both = await session_client.put(
        url, json={"preset": "daily", "cron": "0 9 * * *", "version": rule["version"]}
    )
    assert both.status_code == 422, both.text
    assert both.json()["code"] == "invalid_recurrence"

    changed = await session_client.put(
        url, json={"cron": "0 9 * * 1-5", "version": rule["version"]}
    )
    assert changed.status_code == 200, changed.text
    daily = changed.json()
    assert (daily["id"], daily["preset"], daily["cron"]) == (rule["id"], None, "0 9 * * 1-5")
    # the task keeps its due date: the first weekday 09:00 on or after Friday 2026-03-13
    assert daily["latest_occurrence_on"] == "2026-03-13"
    assert daily["next_due_at"] == "2026-03-16T13:00:00Z"

    unknown = await session_client.get(f"/v1/tasks/{task.project_id}/recurrence")
    assert unknown.status_code == 404, unknown.text

    stale_delete = await session_client.delete(url, params={"version": rule["version"]})
    assert stale_delete.status_code == 409, stale_delete.text
    assert stale_delete.json()["code"] == "stale_version"
    deleted = await session_client.delete(url, params={"version": daily["version"]})
    assert deleted.status_code == 204, deleted.text

    gone = await session_client.get(url)
    assert gone.status_code == 404, gone.text
    empty = await session_client.get("/v1/recurrence", params={"project_id": str(task.project_id)})
    assert empty.json()["items"] == []
