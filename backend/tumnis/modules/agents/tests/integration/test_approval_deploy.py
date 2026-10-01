"""An approval has no deadline and survives a deploy (P2-05, FR-5.6, R-30).

DBOS recovers only the workflows of the version it runs, so a deploy strands the old
version's `approval_flow` and `dispatch_run`. The human's decision reaches the waiting
approval through `human.decided`: the subscriber finds the old version's workflow,
cancels it and starts `approval:<id>:<current version>` to finish it, and the run's
`resumed` signal continues the run on the current version (`supervise_run`).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.agents.tests.integration._human import (
    approval,
    decide,
    human_waits,
    open_items,
    outbox_count,
    run_status,
    started,
    task_status,
)
from tumnis.modules.agents.tests.integration._runs import (
    finish,
    mark_outbox_sent,
    owner_rows,
    relay,
    run_world,
    wait_until,
)

if TYPE_CHECKING:
    from dbos import DBOS
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkerKillerFactory, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]

MERGE = "Merge fix-footer into main"


def _status(client: Any, workflow_id: str) -> str | None:
    """A workflow's DBOS status, read on a plain thread (DBOS's sync API refuses to run
    beside an event loop)."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        found = pool.submit(client.get_workflow_status, workflow_id).result()
    return None if found is None else str(found.status)


@pytest.mark.req("FR-5.6")
@pytest.mark.wp("P2-05")
@pytest.mark.slow
@pytest.mark.xfail(strict=True, reason="spec:P2-05")
async def test_approval_survives_deploy_to_new_version(  # noqa: PLR0917
    app: FastAPI,
    worker_killer: WorkerKillerFactory,
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-05-06
    Given a run parked on an approval opened by a worker on version v1, when v1 dies and
    a worker on v2 starts and the human approves, then the v1 `approval_flow` is
    cancelled and `approval:<id>:v2` finishes it: the re-send returns `approved`, the task
    is back In progress once, and the run continues on v2 to its result.
    """
    from tests.fixtures import KILLED_EXIT  # noqa: PLC0415
    from tumnis.core.clock import SystemClock  # noqa: PLC0415

    app.state.clock = SystemClock()  # the worker's runner sweep reads the real clock
    point = "agents.approval_flow.waiting"
    killer = worker_killer(point, events=0)
    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")
    mark_outbox_sent(db)
    run_id = await world.request(task.id)

    v1 = await killer.start(point, app_version="v1")
    try:
        world.runner.wait_for(lambda r: any(m.run_id == run_id for m in r.runs()), timeout=30)
        token = world.token(run_id)
        with human_waits(poll_seconds=1):
            first = await approval(app, token, run_id, "merge_main", MERGE)
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "pending"
        approval_id = first.json()["id"]
        code = await asyncio.wait_for(v1.wait(), 60)
    except TimeoutError:
        await killer.stop(v1)
        pytest.fail(f"worker not killed at {point}\n{killer.log_tail()}")
    assert code == KILLED_EXIT, killer.log_tail()

    world.runner.heartbeat()
    v2 = await killer.start(None, app_version="v2")
    try:
        assert await wait_until(lambda: run_status(db, run_id) == "waiting_on_human", timeout=60), (
            killer.log_tail()
        )
        [item] = open_items(db, "approval")
        approved = await decide(session_client, item, "approve", {"reason": "Checked the diff"})
        assert approved.status_code == 200, approved.text
        assert await wait_until(lambda: run_status(db, run_id) == "running", timeout=60), (
            killer.log_tail()
        )
        assert task_status(db, task.id) == "in_progress"
        with human_waits(poll_seconds=1):
            again = await approval(app, token, run_id, "merge_main", MERGE, approval_id=approval_id)
        assert again.json()["status"] == "approved"
        assert owner_rows(
            db, "SELECT workflow_id FROM approvals WHERE id = %s", (approval_id,)
        ) == [(f"approval:{approval_id}:v2",)]
        assert _status(killer.dbos_client(), f"approval:{approval_id}:v1") == "CANCELLED"

        finish(world.runner, run_id)
        assert await wait_until(lambda: run_status(db, run_id) == "succeeded", timeout=60), (
            killer.log_tail()
        )
    finally:
        await killer.stop(v2)

    assert outbox_count(db, "task.status_changed", task_id=str(task.id), to="in_progress") == 2
    assert outbox_count(db, "run.finished", run_id=str(run_id)) == 1


@pytest.mark.req("FR-5.6")
@pytest.mark.wp("P2-05")
@pytest.mark.slow
@pytest.mark.xfail(strict=True, reason="spec:P2-05")
async def test_approval_has_no_deadline(  # noqa: PLR0917
    app: FastAPI,
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-05-07
    With the human wait re-armed every second (R-30's WAIT_SLICE_S shortened), after ten
    slices the approval is still pending, its workflow still waits, and the human can
    still approve it: the re-send returns `approved` and the run resumes."""
    from dbos import DBOS  # noqa: PLC0415

    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")
    with human_waits(poll_seconds=0, slice_seconds=1):
        async with relay(db):
            run_id = await started(world, db, task.id)
            token = world.token(run_id)
            first = await approval(app, token, run_id, "merge_main", MERGE)
            approval_id = first.json()["id"]
            assert await wait_until(lambda: run_status(db, run_id) == "waiting_on_human")
            await asyncio.sleep(10.5)

            assert owner_rows(db, "SELECT status FROM approvals WHERE id = %s", (approval_id,)) == [
                ("pending",)
            ]
            [(workflow_id,)] = owner_rows(
                db, "SELECT workflow_id FROM approvals WHERE id = %s", (approval_id,)
            )
            assert _status(DBOS, workflow_id) == "PENDING"
            [item] = open_items(db, "approval")
            approved = await decide(session_client, item, "approve", {"reason": "Still fine"})
            assert approved.status_code == 200, approved.text
            assert await wait_until(lambda: run_status(db, run_id) == "running")
            again = await approval(app, token, run_id, "merge_main", MERGE, approval_id=approval_id)
    assert again.json()["status"] == "approved"
    assert task_status(db, task.id) == "in_progress"
