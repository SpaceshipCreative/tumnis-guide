"""dispatch_run on the `runs` queue, partitioned by project (P2-04, SAF-5, R-23).

Two runs of a project run at once; a third waits in the queue until one finishes, while
another project's runs are not held up. A second Run on a task that already has an
active run is refused.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from tumnis.modules.agents.tests.integration._runs import (
    finish,
    owner_rows,
    relay,
    run_world,
    wait_until,
    workflow_status,
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


def _status(db: DbUrls, run_id: UUID) -> str | None:
    rows = owner_rows(db, "SELECT status FROM runs WHERE id = %s", (run_id,))
    return rows[0][0] if rows else None


@pytest.mark.req("SAF-5")
@pytest.mark.wp("P2-04")
@pytest.mark.xfail(strict=True, reason="spec:P2-04")
async def test_two_runs_per_project_third_waits(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P2-04-01
    Given three AI tasks in "Acme site", when all three are run, then two runs are
    `running` and the third's workflow is still `ENQUEUED` (its run `queued`); when the
    fake runner finishes one, the third becomes `running`.
    """
    world = await run_world(fake_runner, workspace, clock)
    tasks = [await world.ai_task(f"Footer fix {i}") for i in range(3)]
    async with relay(db):
        runs = []
        for task in tasks:
            runs.append(await world.request(task.id))
            await asyncio.sleep(0.05)  # enqueued in this order

        def two_running() -> bool:
            return sum(_status(db, run) == "running" for run in runs) == 2

        assert await wait_until(two_running, timeout=5)
        [waiting] = [run for run in runs if _status(db, run) != "running"]
        await asyncio.sleep(1.5)  # a free slot would have been taken by now
        assert _status(db, waiting) == "queued"
        assert workflow_status(waiting) == "ENQUEUED"
        assert two_running()

        first = next(run for run in runs if run != waiting)
        world.runner.wait_for(lambda r: any(m.run_id == first for m in r.runs()))
        finish(world.runner, first)
        assert await wait_until(lambda: _status(db, first) == "succeeded")
        assert await wait_until(lambda: _status(db, waiting) == "running", timeout=10)
        assert len(world.runner.runs()) == 3


@pytest.mark.req("SAF-5")
@pytest.mark.wp("P2-04")
@pytest.mark.xfail(strict=True, reason="spec:P2-04")
async def test_other_project_not_blocked(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P2-04-02
    With Acme site's partition full (two running, a third queued), a run in another
    project starts at once.
    """
    world = await run_world(fake_runner, workspace, clock, others=(("Beacon app", "beacon-app"),))
    acme = [await world.ai_task(f"Acme {i}") for i in range(3)]
    beacon = await world.ai_task("Beacon login", project_id=world.projects["Beacon app"])
    async with relay(db):
        acme_runs = [await world.request(task.id) for task in acme]

        def acme_full() -> bool:
            return sum(_status(db, run) == "running" for run in acme_runs) == 2

        assert await wait_until(acme_full, timeout=5)
        beacon_run = await world.request(beacon.id)
        assert await wait_until(lambda: _status(db, beacon_run) == "running", timeout=5)
        assert acme_full()
        assert sum(_status(db, run) == "queued" for run in acme_runs) == 1


@pytest.mark.req("SAF-5")
@pytest.mark.wp("P2-04")
@pytest.mark.xfail(strict=True, reason="spec:P2-04")
async def test_double_run_click_refused(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-04-14
    `POST /v1/tasks/{id}/run` answers 202 `{run_id, status: "queued"}`; a second Run on the
    same task while that run is active is 409 `run_already_active`, and no second run is
    made.
    """
    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")
    async with relay(db):
        first = await session_client.post(f"/v1/tasks/{task.id}/run", json={})
        assert first.status_code == 202, first.text
        body = first.json()
        assert body["status"] == "queued"
        run_id = UUID(body["run_id"])

        second = await session_client.post(f"/v1/tasks/{task.id}/run", json={})
        assert second.status_code == 409, second.text
        assert second.json()["code"] == "run_already_active"

        assert await wait_until(lambda: _status(db, run_id) == "running")
        third = await session_client.post(f"/v1/tasks/{task.id}/run", json={})
        assert third.status_code == 409, third.text
        assert third.json()["code"] == "run_already_active"
    assert owner_rows(db, "SELECT count(*) FROM runs WHERE task_id = %s", (task.id,)) == [(1,)]
