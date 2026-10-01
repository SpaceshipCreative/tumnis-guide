"""Stuck handling (P4-02, FR-10.5): "Stuck" on a check-in routes to the task's project
agent, which posts a first step of 10 minutes or less as a subtask or takes the step itself
and reports back; the focus bar shows the step within a minute, or a fallback (the task's
first action with a 10-minute timer) when no answer comes in time.

The person's side goes through the app's routes (`session_client`); the agent's side is
the fake runner speaking protocol 2, calling back with the run's task token.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from tumnis.modules.agents.tests.integration._runs import (
    finish,
    master_key,
    owner_rows,
    relay,
    run_world,
    stream,
    wait_until,
)
from tumnis.modules.agents.tests.integration._stuck import (
    FIRST_ACTION,
    RequestRunSpy,
    call_tool,
    human_task,
    live_messages,
    next_step,
    quiet_stuck_workflows,
    stuck_deadline,
    stuck_runs,
    stuck_workflows,
    tap_again,
    tap_stuck,
)

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]

STUCK_DEADLINE_S = 60  # FR-10.5 "within a minute"


def _status(db: DbUrls, run_id: UUID) -> str | None:
    rows = owner_rows(db, "SELECT status FROM runs WHERE id = %s", (run_id,))
    return rows[0][0] if rows else None


@pytest.mark.req("FR-10.5")
@pytest.mark.wp("P4-02")
async def test_stuck_routes_to_project_agent(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    dbos_sys_db: DbUrls,
    session_client: SessionClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-P4-02-01
    `focus.event(stuck)` on a Human task calls `request_run(task_id, "stuck", priority=1)`
    once; the packet validates against P2-02's schema with `kind: stuck`, the `stuck` skill,
    one task per run, `stuck_step.max_minutes` 10 and the recent comments; it goes to the
    task's project profile, never the master.
    """
    from tumnis.modules.agents.packet_builder import TaskPacket, TaskRunRequest  # noqa: PLC0415

    world = await run_world(fake_runner, workspace, clock)
    await master_key(world)  # a master profile on the same runner
    task = await human_task(world)
    spy = RequestRunSpy()
    spy.install(monkeypatch)
    async with relay(db), quiet_stuck_workflows(dbos_sys_db):
        await tap_stuck(session_client, db, workspace.id, task.id, clock.now())
        assert await wait_until(lambda: len(stuck_runs(db, task.id)) == 1)
        [(run_id, _, profile_id)] = stuck_runs(db, task.id)
        assert await wait_until(lambda: len(world.packets(run_id)) == 1)

    assert spy.calls == [(task.id, "stuck", 1)]
    assert profile_id == world.profile_id
    [raw] = world.packets(run_id)
    packet = TaskPacket.model_validate(raw)
    assert packet.kind == "stuck"
    assert packet.profile_id == world.profile_id
    assert packet.skill == "stuck"
    assert packet.policy is not None
    assert packet.policy.max_tasks_per_run == 1
    body = TaskRunRequest.model_validate(packet.body)
    assert body.task.id == task.id
    assert packet.body["stuck_step"] == {"max_minutes": 10}
    assert isinstance(packet.body["recent_comments"], list)
    assert len(packet.body["recent_comments"]) <= 5


@pytest.mark.xfail(strict=True, reason="spec:P4-02")
@pytest.mark.req("FR-10.5")
@pytest.mark.wp("P4-02")
async def test_split_step_posted_within_a_minute(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    dbos_sys_db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P4-02-02
    The scripted split arrives: a step over 10 minutes is refused (422
    `stuck_step_too_long`), one of 8 minutes is made under the stuck task; a `/ws` message
    goes out and `GET /v1/focus/current` shows it as the next step, before the deadline.
    """
    world = await run_world(fake_runner, workspace, clock)
    task = await human_task(world)
    loop = asyncio.get_running_loop()
    async with relay(db), quiet_stuck_workflows(dbos_sys_db), live_messages(db) as live:
        tapped = loop.time()
        await tap_stuck(session_client, db, workspace.id, task.id, clock.now())
        assert await wait_until(lambda: len(stuck_runs(db, task.id)) == 1)
        [(run_id, _, _)] = stuck_runs(db, task.id)
        assert await wait_until(lambda: len(world.packets(run_id)) == 1)
        step = await next_step(session_client)
        assert step is not None
        assert step["state"] == "working"

        token = world.token(run_id)
        args = {
            "project_id": str(world.project_id),
            "parent_id": str(task.id),
            "title": "Find last month's invoice to copy",
            "label": "human",
        }
        too_long = await call_tool(
            world.runner, token, "create_task", {**args, "estimate_minutes": 11}
        )
        assert too_long.status == 422, too_long
        assert too_long.code == "stuck_step_too_long"
        made = await call_tool(world.runner, token, "create_task", {**args, "estimate_minutes": 8})
        assert made.status == 200, made
        step_id = UUID(made.data["id"])

        async def shown() -> bool:
            found = await next_step(session_client)
            return found is not None and found["state"] == "split"

        assert await wait_until(shown)
        elapsed = loop.time() - tapped
        step = await next_step(session_client)
        await asyncio.sleep(0.5)  # the notification of the step's commit reaches LISTEN
    assert elapsed < STUCK_DEADLINE_S
    assert step is not None
    assert step["task_id"] == str(task.id)
    assert step["run_id"] == str(run_id)
    assert step["step"]["task_id"] == str(step_id)
    assert step["step"]["estimate_minutes"] <= 10
    assert step["step"]["title"] == "Find last month's invoice to copy"
    [(parent, estimate)] = owner_rows(
        db, "SELECT parent_id, estimate_minutes FROM tasks WHERE id = %s", (step_id,)
    )
    assert (parent, estimate) == (task.id, 8)
    assert any(m["entity"] == "focus" for m in live)


@pytest.mark.xfail(strict=True, reason="spec:P4-02")
@pytest.mark.req("FR-10.5")
@pytest.mark.wp("P4-02")
async def test_agent_takes_step_itself(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    dbos_sys_db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P4-02-03
    The agent takes the AI-able step itself: a scripted comment (a log line) and a result.
    A review item of kind `result` names the run, the focus bar shows the report as the
    next step, and the stuck task keeps its status (the person is still on it).
    """
    world = await run_world(fake_runner, workspace, clock)
    task = await human_task(world)
    async with relay(db), quiet_stuck_workflows(dbos_sys_db):
        await tap_stuck(session_client, db, workspace.id, task.id, clock.now())
        assert await wait_until(lambda: len(stuck_runs(db, task.id)) == 1)
        [(run_id, _, _)] = stuck_runs(db, task.id)
        assert await wait_until(lambda: len(world.packets(run_id)) == 1)
        world.runner.wait_for(lambda r: any(m.run_id == run_id for m in r.runs()))
        stream(world.runner, run_id, 1, "Found the February invoice; copying it")
        finish(
            world.runner,
            run_id,
            {
                "outcome": "done",
                "summary": "Drafted the March invoice from February's",
                "files_touched": [],
                "links": [],
            },
        )
        assert await wait_until(lambda: _status(db, run_id) == "succeeded")

        async def reported() -> bool:
            found = await next_step(session_client)
            return found is not None and found["state"] == "took_step"

        assert await wait_until(reported)
        step = await next_step(session_client)
    assert step is not None
    assert step["summary"] == "Drafted the March invoice from February's"
    assert step["step"] is None
    items = owner_rows(
        db,
        "SELECT kind, payload->>'run_id' FROM review_items WHERE target_id = %s",
        (task.id,),
    )
    assert ("result", str(run_id)) in items
    [(status,)] = owner_rows(db, "SELECT status FROM tasks WHERE id = %s", (task.id,))
    assert status == task.status


@pytest.mark.req("FR-10.5")
@pytest.mark.wp("P4-02")
async def test_stuck_jumps_queue_priority(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    dbos_sys_db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P4-02-05
    With both of the project's slots held by long runs and a normal run R2 queued, a stuck
    run S queued after R2 starts first when a slot frees (DBOS queue priority within the
    project's partition).
    """
    world = await run_world(fake_runner, workspace, clock)
    long_tasks = [await world.ai_task(f"Long job {i}") for i in range(2)]
    queued_task = await world.ai_task("Normal job")
    task = await human_task(world)
    async with relay(db), quiet_stuck_workflows(dbos_sys_db):
        held = [await world.request(t.id) for t in long_tasks]

        def both_running() -> bool:
            return all(_status(db, run) == "running" for run in held)

        assert await wait_until(both_running, timeout=10)
        normal = await world.request(queued_task.id)
        await asyncio.sleep(1.0)  # R2 is enqueued well before S
        assert _status(db, normal) == "queued"

        await tap_stuck(session_client, db, workspace.id, task.id, clock.now())
        assert await wait_until(lambda: len(stuck_runs(db, task.id)) == 1)
        [(stuck_run, _, _)] = stuck_runs(db, task.id)
        await asyncio.sleep(1.0)  # S sits in the queue behind the full partition
        assert _status(db, stuck_run) == "queued"

        world.runner.wait_for(lambda r: any(m.run_id == held[0] for m in r.runs()))
        finish(world.runner, held[0])
        assert await wait_until(lambda: _status(db, stuck_run) == "running", timeout=10)
        assert _status(db, normal) == "queued"


@pytest.mark.req("FR-10.5")
@pytest.mark.wp("P4-02")
async def test_no_answer_in_60s_shows_fallback(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    dbos_sys_db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P4-02-06
    The runner stays silent and `stuck_deadline_seconds` is 1: after the deadline the focus
    bar shows the fallback, the task's first action with a 10-minute timer; a late step
    still lands and replaces it.
    """
    world = await run_world(fake_runner, workspace, clock)
    task = await human_task(world)
    with stuck_deadline(1):
        async with relay(db), quiet_stuck_workflows(dbos_sys_db):
            await tap_stuck(session_client, db, workspace.id, task.id, clock.now())
            assert await wait_until(lambda: len(stuck_runs(db, task.id)) == 1)
            [(run_id, _, _)] = stuck_runs(db, task.id)
            assert await wait_until(lambda: len(world.packets(run_id)) == 1)

            async def fell_back() -> bool:
                found = await next_step(session_client)
                return found is not None and found["state"] == "fallback"

            assert await wait_until(fell_back, timeout=15)
            fallback = await next_step(session_client)
            assert fallback is not None
            assert fallback["first_action"] == FIRST_ACTION
            assert fallback["timer_minutes"] == 10
            assert fallback["step"] is None

            made = await call_tool(
                world.runner,
                world.token(run_id),
                "create_task",
                {
                    "project_id": str(world.project_id),
                    "parent_id": str(task.id),
                    "title": "Open February's invoice",
                    "label": "human",
                    "estimate_minutes": 5,
                },
            )
            assert made.status == 200, made

            async def replaced() -> bool:
                found = await next_step(session_client)
                return found is not None and found["state"] == "split"

            assert await wait_until(replaced)
            late = await next_step(session_client)
    assert late is not None
    assert late["step"]["title"] == "Open February's invoice"


@pytest.mark.req("REL-2")
@pytest.mark.wp("P4-02")
async def test_double_stuck_tap_dispatches_once(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    dbos_sys_db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P4-02-07
    Two stuck responses for one check-in, seconds apart: one `handle_stuck` workflow
    (`stuck:<focus event id>`) and one stuck run.
    """
    world = await run_world(fake_runner, workspace, clock)
    task = await human_task(world)
    async with relay(db), quiet_stuck_workflows(dbos_sys_db):
        check_in, stuck_event = await tap_stuck(
            session_client, db, workspace.id, task.id, clock.now()
        )
        clock.advance(seconds=5)
        await tap_again(session_client, check_in)
        assert await wait_until(lambda: len(stuck_runs(db, task.id)) == 1)
        await asyncio.sleep(1.5)  # a second workflow or run would have started by now
        assert stuck_workflows(dbos_sys_db) == [f"stuck:{stuck_event}"]
        assert len(stuck_runs(db, task.id)) == 1
