"""Delegation (P2-06, FR-5.2, SAF-5, design decisions 5 and 6): only the master's key hands a
task to a project agent (`delegate_task`), the child run's workflow ID is the delegation
ID, `wait_for_task` answers `done`, `waiting_on_human` or `still_running` within its
timeout, and depth and loop limits stop runaway delegation.

The tests play both agents over MCP: the master with its key, a child with its run's task
token (`_delegation.py`).
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest

from tests.acceptance._phase2 import post_result, tool, wait_id
from tumnis.modules.agents.tests.integration._delegation import (
    child_task,
    delegate,
    delegation_rows,
    key_without_delegate,
    open_items,
    project_key_with_delegate,
    run_row,
    runs_of_task,
    start_master_run,
    stop,
    wait,
    world,
)
from tumnis.modules.agents.tests.integration._human import human_waits
from tumnis.modules.agents.tests.integration._runs import relay, wait_until, workflow_status

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]

QUESTION = "Should the footer link open in a new tab?"
SUMMARY = "The footer link opens the right page"


def _ended(db: DbUrls, run_id: UUID) -> bool:
    row = run_row(db, run_id)
    return row is not None and row[0] not in ("queued", "held", "running", "waiting_on_human")


@pytest.mark.req("FR-5.2")
@pytest.mark.wp("P2-06")
async def test_only_master_key_with_delegate_can_delegate(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P2-06-01
    The master's key delegates a task (a delegation id, its project, depth 1). A key that
    holds `delegate` but belongs to the project's agent gets 403 `master_only`; a key
    without `delegate` gets 403 `insufficient_scope`. Neither makes a delegation."""
    w = await world(fake_runner, workspace, clock)
    task = await w.ai_task("Fix footer link")
    other = await w.ai_task("Fix header link")

    allowed = await delegate(w, w.master_key or "", task)
    assert allowed.ok, allowed
    assert UUID(allowed.data["delegation_id"])
    assert allowed.data["project_id"] == str(w.project_id)
    assert allowed.data["depth"] == 1

    refused = await delegate(w, await project_key_with_delegate(w), other)
    assert refused.code == "master_only", refused
    no_scope = await delegate(w, await key_without_delegate(w), other)
    assert no_scope.code == "insufficient_scope", no_scope
    assert [row[1] for row in delegation_rows(db)] == [task]


@pytest.mark.req("FR-5.2")
@pytest.mark.wp("P2-06")
async def test_child_workflow_id_is_delegation_id(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P2-06-02
    `DBOS.retrieve_workflow(delegation_id)` is the child's `dispatch_run`, enqueued on the
    child's project partition; the run row's id and workflow ID are the delegation ID."""
    from dbos import DBOS  # noqa: PLC0415

    w = await world(fake_runner, workspace, clock)
    task = await w.ai_task("Fix footer link")
    async with relay(db):
        delegated = await delegate(w, w.master_key or "", task)
        assert delegated.ok, delegated
        delegation_id = UUID(delegated.data["delegation_id"])
        await w.delivered(delegation_id)
        assert await wait_until(lambda: workflow_status(delegation_id) is not None)
        handle: Any = await DBOS.retrieve_workflow_async(str(delegation_id))
        status = await handle.get_status()
    assert status.name == "dispatch_run"
    assert status.queue_partition_key == str(w.project_id)
    row = run_row(db, delegation_id)
    assert row is not None
    assert row[2] == str(delegation_id)
    assert row[3] == delegation_id


@pytest.mark.req("Design decision 6")
@pytest.mark.wp("P2-06")
async def test_wait_returns_done_after_result(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P2-06-03
    The child posts its result: `wait_for_task` answers `done`, run status `succeeded`, the
    result's summary and the task's status (In review)."""
    w = await world(fake_runner, workspace, clock)
    task = await w.ai_task("Fix footer link")
    async with relay(db):
        delegated = await delegate(w, w.master_key or "", task)
        assert delegated.ok, delegated
        delegation_id = UUID(delegated.data["delegation_id"])
        await w.delivered(delegation_id)
        posted = await post_result(w.runner, w.token(delegation_id), delegation_id, SUMMARY)
        assert posted.ok, posted
        done = await wait(w, w.master_key or "", delegation_id, 30)
    assert done.ok, done
    assert done.data["status"] == "done"
    assert done.data["run_status"] == "succeeded"
    assert done.data["result_summary"] == SUMMARY
    assert done.data["task_status"] == "in_review"
    assert done.data["question"] is None


@pytest.mark.req("Design decision 6")
@pytest.mark.wp("P2-06")
async def test_wait_returns_waiting_on_human_before_timeout(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P2-06-04
    The master waits (timeout 30 s) while the child asks a question: the wait answers
    `waiting_on_human` with the question's text in under 5 s from the ask."""
    w = await world(fake_runner, workspace, clock)
    task = await w.ai_task("Fix footer link")
    with human_waits(poll_seconds=1):
        async with relay(db):
            delegated = await delegate(w, w.master_key or "", task)
            assert delegated.ok, delegated
            delegation_id = UUID(delegated.data["delegation_id"])
            await w.delivered(delegation_id)
            waiting = asyncio.create_task(wait(w, w.master_key or "", delegation_id, 30))
            asked_at = time.monotonic()
            asked = await tool(
                w.runner,
                w.token(delegation_id),
                "ask_human",
                {"run_id": str(delegation_id), "prompt": QUESTION},
            )
            answer = await waiting
            waited = time.monotonic() - asked_at
    assert asked.ok, asked
    assert wait_id(asked.data)
    assert answer.ok, answer
    assert answer.data["status"] == "waiting_on_human"
    assert answer.data["run_status"] == "waiting_on_human"
    assert answer.data["question"] == QUESTION
    assert waited < 5


@pytest.mark.req("Design decision 6")
@pytest.mark.wp("P2-06")
async def test_wait_returns_still_running_at_timeout(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P2-06-05
    `timeout_seconds=2` while the child is busy: `still_running` (run status `running`)
    after about 2 s, never parked longer."""
    w = await world(fake_runner, workspace, clock)
    task = await w.ai_task("Fix footer link")
    async with relay(db):
        delegated = await delegate(w, w.master_key or "", task)
        assert delegated.ok, delegated
        delegation_id = UUID(delegated.data["delegation_id"])
        await w.delivered(delegation_id)
        started = time.monotonic()
        busy = await wait(w, w.master_key or "", delegation_id, 2)
        took = time.monotonic() - started
    assert busy.ok, busy
    assert busy.data["status"] == "still_running"
    assert busy.data["run_status"] == "running"
    assert 1.5 <= took < 5


@pytest.mark.req("SAF-5")
@pytest.mark.wp("P2-06")
async def test_depth_three_refused_end_to_end(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P2-06-07
    The master delegates T1; its child run creates subtask T2 with its task token; the
    master delegates T2 (depth 2); that child creates T3; delegating T3 would be depth 3 and
    is 409 `delegation_depth_exceeded`, with no delegation or run. The caller is the
    master's key every time: depth comes from the task's chain."""
    w = await world(fake_runner, workspace, clock)
    master = w.master_key or ""
    t1 = await w.ai_task("Launch the landing page")
    async with relay(db):
        first = await delegate(w, master, t1)
        assert first.ok, first
        d1 = UUID(first.data["delegation_id"])
        await w.delivered(d1)
        made = await child_task(w, w.token(d1), "Write the hero copy", parent_id=t1)
        assert made.ok, made
        t2 = UUID(made.data["id"])

        second = await delegate(w, master, t2)
        assert second.ok, second
        assert second.data["depth"] == 2
        d2 = UUID(second.data["delegation_id"])
        await w.delivered(d2)
        made = await child_task(w, w.token(d2), "Pick the hero image")
        assert made.ok, made
        t3 = UUID(made.data["id"])

        third = await delegate(w, master, t3)
    assert third.code == "delegation_depth_exceeded", third
    assert [(row[1], row[2]) for row in delegation_rows(db)] == [(t1, 1), (t2, 2)]
    assert runs_of_task(db, t3) == []


@pytest.mark.req("SAF-5")
@pytest.mark.wp("P2-06")
async def test_loop_stops_run_with_review_item(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P2-06-09
    The master delegates the same task a third time within the window, with no accepted
    result between: 409 `delegation_loop`, one open `delegation_loop` review item on the
    task, and no third child run (R-34). The master was calling from inside its own run,
    which is stopped: `cancelled`, reason `delegation_loop`."""
    w = await world(fake_runner, workspace, clock)
    master = w.master_key or ""
    task = await w.ai_task("Fix footer link")
    async with relay(db):
        master_run = await start_master_run(w, db)
        children: list[UUID] = []
        for _ in range(2):
            delegated = await delegate(w, master, task)
            assert delegated.ok, delegated
            child = UUID(delegated.data["delegation_id"])
            await w.delivered(child)
            await stop(w, child)
            assert await wait_until(lambda child=child: _ended(db, child))
            children.append(child)

        looped = await delegate(w, master, task)
        assert looped.code == "delegation_loop", looped
        assert await wait_until(lambda: _ended(db, master_run))
    items: list[dict[str, Any]] = open_items(db, "delegation_loop", task)
    assert len(items) == 1
    assert sorted(runs_of_task(db, task)) == sorted(children)
    master_row = run_row(db, master_run)
    assert master_row is not None
    assert master_row[:2] == ("cancelled", "delegation_loop")
