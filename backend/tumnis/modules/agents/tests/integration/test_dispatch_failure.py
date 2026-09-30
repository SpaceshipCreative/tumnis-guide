"""Run failure paths (P2-04, NFR Reliability): a runner that stops beating mid-run fails
the run as `runner_lost`, and the task stays In progress with the run's log."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.agents.tests.integration._runs import (
    owner_rows,
    relay,
    run_world,
    stream,
    wait_until,
)

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


@pytest.mark.req("NFR Reliability")
@pytest.mark.wp("P2-04")
async def test_dropped_runner_fails_run_task_stays_in_progress(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P2-04-08
    The runner misses three heartbeats while the run is running: the runner sweep marks it
    offline and the run ends `runner_lost`; the task stays `in_progress`, and the run's log
    (its stream lines and a closing `runner_lost` line) is kept.
    """
    from tumnis.modules.agents import workflows  # noqa: PLC0415

    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")
    async with relay(db):
        run_id = await world.request(task.id)
        world.runner.wait_for(lambda r: any(m.run_id == run_id for m in r.runs()))
        assert await wait_until(
            lambda: (
                owner_rows(db, "SELECT status FROM runs WHERE id = %s", (run_id,)) == [("running",)]
            )
        )
        stream(world.runner, run_id, 1, "Editing src/footer.tsx")
        assert await wait_until(
            lambda: (
                owner_rows(
                    db,
                    "SELECT count(*) FROM run_events WHERE run_id = %s AND kind = 'log'",
                    (run_id,),
                )
                == [(1,)]
            )
        )
        # Three beats missed: the sweep runs 46 s after the runner's last heartbeat.
        handle = await dbos.start_workflow_async(
            workflows.runner_sweep, clock.now() + timedelta(seconds=46), None
        )
        await handle.get_result()
        assert await wait_until(
            lambda: (
                owner_rows(db, "SELECT status FROM runs WHERE id = %s", (run_id,))
                == [("runner_lost",)]
            ),
            timeout=15,
        )

    assert owner_rows(db, "SELECT stop_reason FROM runs WHERE id = %s", (run_id,)) == [
        ("runner_lost",)
    ]
    assert owner_rows(db, "SELECT status::text FROM tasks WHERE id = %s", (task.id,)) == [
        ("in_progress",)
    ]
    texts = [
        payload.get("text")
        for (payload,) in owner_rows(
            db, "SELECT payload FROM run_events WHERE run_id = %s ORDER BY seq", (run_id,)
        )
    ]
    assert "Editing src/footer.tsx" in texts
    assert texts[-1] == "Stopped: the runner stopped answering"
    assert owner_rows(
        db,
        "SELECT payload->>'status' FROM outbox WHERE name = 'run.finished'"
        " AND payload->>'run_id' = %s",
        (str(run_id),),
    ) == [("runner_lost",)]
