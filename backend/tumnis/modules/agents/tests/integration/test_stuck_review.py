"""A stuck run's result, reviewed (Scott decision 73, FR-10.5).

When the person is stuck and the project agent takes the next step itself, its report
waits in the review queue as a `result` item while the person stays on the task. Accepting
it marks the stuck step done and keeps the task In progress, so the focus session carries
on. Rejecting it reopens the step for the person and records the reason as a comment in
the task's history; no new run starts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.agents.tests.integration._human import decide, open_items
from tumnis.modules.agents.tests.integration._runs import (
    finish,
    owner_rows,
    relay,
    run_world,
    wait_until,
)
from tumnis.modules.agents.tests.integration._stuck import (
    FIRST_ACTION,
    human_task,
    next_step,
    quiet_stuck_workflows,
    stuck_runs,
    tap_stuck,
)

if TYPE_CHECKING:
    from uuid import UUID

    from dbos import DBOS

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.agents.tests.integration._runs import RunWorld

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]

REPORT = "Drafted the March invoice from February's"
REASON = "The draft uses last year's rates"


def _states(db: DbUrls, task_id: UUID) -> list[str]:
    found = owner_rows(
        db, "SELECT state FROM stuck_requests WHERE task_id = %s ORDER BY created_at", (task_id,)
    )
    return [state for (state,) in found]


def _status(db: DbUrls, task_id: UUID) -> str:
    [(status,)] = owner_rows(db, "SELECT status::text FROM tasks WHERE id = %s", (task_id,))
    return str(status)


def _runs_of(db: DbUrls, task_id: UUID) -> list[tuple[Any, ...]]:
    return owner_rows(db, "SELECT id, kind FROM runs WHERE task_id = %s", (task_id,))


async def _started_task(world: RunWorld) -> Any:
    """The person's Human task, started (In progress), as during a focus session."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    task = await human_task(world)
    async with tenant_session(world.ctx) as s:
        return await tasks.change_status(
            s,
            world.ctx.actor,
            task.id,
            tasks.Status.IN_PROGRESS,
            task.version,
            now=world.clock.now(),
        )


async def _took_step(
    world: RunWorld, db: DbUrls, http: SessionClient, workspace: WorkspaceHandle, task_id: UUID
) -> dict[str, Any]:
    """The person taps Stuck, and the agent takes the step itself and reports; its open
    `result` review item."""
    await tap_stuck(http, db, workspace.id, task_id, world.clock.now())
    assert await wait_until(lambda: len(stuck_runs(db, task_id)) == 1)
    [(run_id, _, _)] = stuck_runs(db, task_id)
    assert await wait_until(lambda: len(world.packets(run_id)) == 1)
    world.runner.wait_for(lambda r: any(m.run_id == run_id for m in r.runs()))
    finish(
        world.runner,
        run_id,
        {"outcome": "done", "summary": REPORT, "files_touched": [], "links": []},
    )
    assert await wait_until(lambda: _states(db, task_id) == ["took_step"])
    [item] = [i for i in open_items(db, "result") if i["target_id"] == task_id]
    return item


@pytest.mark.req("FR-10.5")
@pytest.mark.wp("P4-02")
@pytest.mark.xfail(strict=True, raises=AssertionError, reason="spec:FIX-stuck-review")
async def test_accepted_stuck_result_marks_step_done(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    dbos_sys_db: DbUrls,
    session_client: SessionClient,
) -> None:
    """Accepting a stuck run's `result` item marks the stuck step done (the focus bar
    shows it) and keeps the task In progress: no Done, no new run, no comment."""
    world = await run_world(fake_runner, workspace, clock)
    task = await _started_task(world)
    async with relay(db), quiet_stuck_workflows(dbos_sys_db):
        item = await _took_step(world, db, session_client, workspace, task.id)
        decided = await decide(session_client, item, "accept")
        assert decided.status_code == 200, decided.text
        assert await wait_until(lambda: _states(db, task.id) == ["done"])
        shown = await next_step(session_client)
    assert shown is not None
    assert shown["state"] == "done"
    assert shown["summary"] == REPORT
    assert _status(db, task.id) == "in_progress"
    assert [kind for _, kind in _runs_of(db, task.id)] == ["stuck"]
    assert owner_rows(db, "SELECT 1 FROM task_comments WHERE task_id = %s", (task.id,)) == []


@pytest.mark.req("FR-10.5")
@pytest.mark.wp("P4-02")
@pytest.mark.xfail(strict=True, raises=AssertionError, reason="spec:FIX-stuck-review")
async def test_rejected_stuck_result_reopens_step_with_reason(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    dbos_sys_db: DbUrls,
    session_client: SessionClient,
) -> None:
    """Rejecting a stuck run's `result` item reopens the step for the person (the focus
    bar shows the task's first action again), records the reason as the person's comment
    on the task, keeps the task In progress and starts no new run."""
    world = await run_world(fake_runner, workspace, clock)
    task = await _started_task(world)
    async with relay(db), quiet_stuck_workflows(dbos_sys_db):
        item = await _took_step(world, db, session_client, workspace, task.id)
        decided = await decide(session_client, item, "reject", {"feedback": REASON})
        assert decided.status_code == 200, decided.text
        assert await wait_until(lambda: _states(db, task.id) == ["reopened"])
        shown = await next_step(session_client)
    assert shown is not None
    assert shown["state"] == "reopened"
    assert shown["first_action"] == FIRST_ACTION
    assert _status(db, task.id) == "in_progress"
    assert [kind for _, kind in _runs_of(db, task.id)] == ["stuck"]
    comments = owner_rows(
        db, "SELECT body_md, created_by FROM task_comments WHERE task_id = %s", (task.id,)
    )
    assert comments == [(REASON, f"user:{workspace.user_id}")]
