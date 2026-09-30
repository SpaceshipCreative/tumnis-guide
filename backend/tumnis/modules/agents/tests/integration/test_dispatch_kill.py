"""dispatch_run survives a killed worker (P2-04, NFR Reliability, AGENTS.md rule 9).

A worker subprocess relays `run.requested`, runs `dispatch_run` and is killed at a named
point; a fresh worker recovers the workflow, which finishes exactly once: one `run`
message to the runner, one result, one `run.finished`.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from tests._pg import APP
from tumnis.modules.agents.tests.integration._runs import (
    finish,
    mark_outbox_sent,
    owner_rows,
    run_world,
    wait_until,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkerKillerFactory, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]

KILL_POINTS = {
    "after_send": "agents.dispatch_run.after_send",  # send_to_agent's output recorded
    "inside_send": "agents.send_to_agent.after_dispatch",  # dispatched, step not recorded
    "waiting_recv": "agents.dispatch_run.waiting_recv",  # parked in recv for the result
}


@pytest.mark.req("NFR Reliability")
@pytest.mark.wp("P2-04")
@pytest.mark.slow
@pytest.mark.xfail(strict=True, reason="spec:P2-04")
@pytest.mark.parametrize("point", list(KILL_POINTS))
async def test_killed_worker_resumes_and_finishes_once(  # noqa: PLR0917
    point: str,
    app: FastAPI,
    worker_killer: WorkerKillerFactory,
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P2-04-05 (after_send), T-P2-04-06 (inside_send), T-P2-04-07 (waiting_recv)
    Killed after `send_to_agent` was recorded, inside it after the adapter dispatched, or
    while waiting in `recv`: after the restart the runner has received the run once, its
    one result finishes the run once (`succeeded`, one `results` row, one `run.finished`).
    """
    from tests.fixtures import KILLED_EXIT  # noqa: PLC0415
    from tumnis.core.clock import SystemClock  # noqa: PLC0415

    # The worker's runner sweep judges heartbeats on the real clock: stamp them on it too.
    app.state.clock = SystemClock()
    killer = worker_killer(KILL_POINTS[point], events=0)
    assert killer.sys_db.url(APP)
    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")
    mark_outbox_sent(db)

    run_id = await world.request(task.id)
    armed = await killer.start(KILL_POINTS[point])
    try:
        code = await asyncio.wait_for(armed.wait(), 60)
    except TimeoutError:
        await killer.stop(armed)
        pytest.fail(f"worker not killed at {KILL_POINTS[point]}\n{killer.log_tail()}")
    assert code == KILLED_EXIT, killer.log_tail()
    assert owner_rows(
        db,
        "SELECT count(*) FROM runner_messages WHERE direction = 'out' AND type = 'run'"
        " AND payload->>'run_id' = %s",
        (str(run_id),),
    ) == [(1,)]

    world.runner.heartbeat()
    worker = await killer.start(None)
    try:
        world.runner.wait_for(lambda r: any(m.run_id == run_id for m in r.runs()), timeout=30)
        finish(world.runner, run_id)
        done = await wait_until(
            lambda: (
                owner_rows(db, "SELECT status FROM runs WHERE id = %s", (run_id,))
                == [("succeeded",)]
            ),
            timeout=60,
        )
        assert done, killer.log_tail()
        assert await wait_until(
            lambda: (
                owner_rows(
                    db,
                    "SELECT count(*) FROM outbox WHERE name = 'run.finished'"
                    " AND payload->>'run_id' = %s",
                    (str(run_id),),
                )
                == [(1,)]
            )
        )
    finally:
        await killer.stop(worker)

    assert len([m for m in world.runner.runs() if m.run_id == run_id]) == 1
    assert owner_rows(db, "SELECT count(*) FROM results WHERE run_id = %s", (run_id,)) == [(1,)]
    assert owner_rows(
        db,
        "SELECT count(*) FROM outbox WHERE name = 'run.finished' AND payload->>'run_id' = %s",
        (str(run_id),),
    ) == [(1,)]
    assert owner_rows(
        db,
        "SELECT count(*) FROM runner_messages WHERE direction = 'out' AND type = 'run'"
        " AND payload->>'run_id' = %s",
        (str(run_id),),
    ) == [(1,)]
