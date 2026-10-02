"""The unattended tick (P4-04, FR-4.5, SAF-1, REL-3): every 5 minutes, in each workspace
whose window is open (in its own timezone), the queued tasks are taken in queued order; a
green-light task gets one run through `agents.api.request_run(..., unattended=True)` (R-23)
and its queue flag is consumed in the same transaction; any other gets one
`unattended_refused` review item per night saying why, and no run. A task is not started
when less than half the project's maximum run time is left in the window.

Night: Monday 2026-03-09 22:00 to Tuesday 06:00 in New York (the `workspace` fixture), EDT.
`request_run` is a spy here (P2-04's own tests prove the run it makes)."""

from __future__ import annotations

from datetime import time, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from tumnis.modules.planning.tests.integration._plan import new_project, user_ctx
from tumnis.modules.planning.tests.integration._unattended import (
    MONDAY_NIGHT,
    TUESDAY_NIGHT,
    WINDOW_END,
    ai_task,
    max_run_minutes,
    queue,
    queued_at,
    review_items,
    set_window,
    spy_runs,
    taint,
    tick,
    tick_workflow,
)

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

REFUSED = "unattended_refused"


@pytest.mark.req("FR-4.5")
@pytest.mark.wp("P4-04")
async def test_only_green_light_ai_tasks_run(
    db: DbUrls, workspace: WorkspaceHandle, clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-P4-04-05
    A mixed queue: an untainted AI task with acceptance criteria, a tainted one, one with no
    acceptance criteria, and an AI task nobody queued. Only the first gets a run, through
    `request_run(task, "task", unattended=True)`; nothing runs before the window opens.
    """
    spy = spy_runs(monkeypatch)
    project = await new_project(workspace, clock, "Acme site")
    await set_window(workspace, clock)
    green = await ai_task(workspace, clock, project, "Fix footer link")
    tainted = await ai_task(workspace, clock, project, "Reply to the client's request")
    taint(db, tainted)
    vague = await ai_task(workspace, clock, project, "Tidy the styles", criteria=None)
    await ai_task(workspace, clock, project, "Not queued")
    for task in (green, tainted, vague):
        await queue(workspace, clock, task)

    await tick(MONDAY_NIGHT - timedelta(minutes=10))  # 21:55: the window is shut
    assert spy.calls == []

    await tick(MONDAY_NIGHT)

    assert spy.calls == [(green, "task", True)]
    assert queued_at(db, green) is None  # the flag is consumed
    assert queued_at(db, tainted) is not None  # refused tasks stay queued
    assert queued_at(db, vague) is not None
    reasons = {item["target_id"]: item["payload"]["refusal"] for item in review_items(db, REFUSED)}
    assert reasons == {tainted: "tainted", vague: "no_acceptance_criteria"}


@pytest.mark.req("SAF-1")
@pytest.mark.wp("P4-04")
async def test_tainted_refused_with_review_item(
    db: DbUrls, workspace: WorkspaceHandle, clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-P4-04-06
    A queued tainted task never runs: every tick of the night refuses it, and it gets one
    `unattended_refused` review item per night (reason `tainted`, the plain words, batched
    into the morning review), a second one the next night.
    """
    spy = spy_runs(monkeypatch)
    project = await new_project(workspace, clock, "Acme site")
    await set_window(workspace, clock)
    task = await ai_task(workspace, clock, project, "Reply to the client's request")
    taint(db, task)
    await queue(workspace, clock, task)

    for minutes in (0, 5, 10, 60):
        await tick(MONDAY_NIGHT + timedelta(minutes=minutes))

    assert spy.calls == []
    [item] = review_items(db, REFUSED, task)
    assert item["target_type"] == "task"
    assert item["project_id"] == project
    assert item["payload"]["refusal"] == "tainted"
    assert item["payload"]["reason"] == "From outside content: needs you"
    assert item["payload"]["batch"] == "overnight"

    await tick(TUESDAY_NIGHT)

    assert spy.calls == []
    assert len(review_items(db, REFUSED, task)) == 2


@pytest.mark.req("REL-3")
@pytest.mark.wp("P4-04")
async def test_tick_dispatches_once_per_window(
    db: DbUrls,
    dbos: Any,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-P4-04-07
    Many ticks inside one window, both the scheduled workflow and the test route's tick:
    one `request_run` per queued task, because the queue flag is consumed in the same
    transaction as the request. Two queued tasks start in queued order.
    """
    spy = spy_runs(monkeypatch)
    project = await new_project(workspace, clock, "Acme site")
    await set_window(workspace, clock)
    first = await ai_task(workspace, clock, project, "Fix footer link")
    second = await ai_task(workspace, clock, project, "Update the sitemap")
    await queue(workspace, clock, first)
    clock.advance(timedelta(minutes=1))
    await queue(workspace, clock, second)

    for minutes in (0, 5, 10):
        await tick_workflow(MONDAY_NIGHT + timedelta(minutes=minutes))
        await tick(MONDAY_NIGHT + timedelta(minutes=minutes, seconds=30))

    assert spy.tasks() == [first, second]
    assert all(unattended for _, _, unattended in spy.calls)
    assert queued_at(db, first) is None
    assert queued_at(db, second) is None
    assert review_items(db, REFUSED) == []


@pytest.mark.req("FR-4.5")
@pytest.mark.wp("P4-04")
async def test_no_start_near_window_end(
    db: DbUrls, workspace: WorkspaceHandle, clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-P4-04-08
    With less than half the project's maximum run time left before the window ends, a
    green-light task is not started and stays queued, without a refusal item; with exactly
    half left it starts.
    """
    spy = spy_runs(monkeypatch)
    project = await new_project(workspace, clock, "Acme site")
    await set_window(workspace, clock)
    task = await ai_task(workspace, clock, project, "Fix footer link")
    await queue(workspace, clock, task)
    half = timedelta(minutes=await max_run_minutes(workspace, project) / 2)

    await tick(WINDOW_END - half + timedelta(minutes=1))

    assert spy.calls == []
    assert queued_at(db, task) is not None
    assert review_items(db, REFUSED) == []

    await tick(WINDOW_END - half)

    assert spy.tasks() == [task]
    assert queued_at(db, task) is None


@pytest.mark.req("FR-4.5")
@pytest.mark.wp("P4-04")
async def test_window_put_for_unseen_project_is_404_first(
    workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """A window PUT naming a project the caller cannot see (another workspace's, which RLS
    hides exactly like one that does not exist) answers 404 even when the window itself
    would be refused (a repeated weekday), so the answer says no more than "not found"
    (A0.3's sweep sends such bodies)."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.core.versioning import NotFound  # noqa: PLC0415
    from tumnis.modules.planning import api as planning  # noqa: PLC0415

    ctx = user_ctx(workspace)
    body = planning.UnattendedWindowIn(
        project_id=uuid4(),
        window=planning.WindowSpec(weekdays=[1, 1], start_local=time(22), end_local=time(6)),
        version=None,
    )
    async with tenant_session(ctx) as s:
        with pytest.raises(NotFound):
            await planning.put_unattended_window(ctx, body, now=clock.now(), session=s)
