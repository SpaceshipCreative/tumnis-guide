"""Housekeeping for runs (P2-04, NFR Reliability): `reconcile_runs`, hourly on the
maintenance queue, fails a run whose `dispatch_run` workflow ended (cancelled or errored)
while its row still says it is active. Nothing else would end it: its workflow no longer
listens for a result or a stop."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.agents.tests.integration._runs import (
    owner_rows,
    relay,
    run_world,
    wait_until,
)

if TYPE_CHECKING:
    from uuid import UUID

    from dbos import DBOS

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


async def _running(world: RunWorld, db: DbUrls, title: str) -> UUID:
    task = await world.ai_task(title)
    run_id = await world.request(task.id)
    world.runner.wait_for(lambda r: any(m.run_id == run_id for m in r.runs()))
    assert await wait_until(
        lambda: owner_rows(db, "SELECT status FROM runs WHERE id = %s", (run_id,)) == [("running",)]
    )
    return run_id


@pytest.mark.req("NFR Reliability")
@pytest.mark.wp("P2-04")
async def test_reconcile_fails_a_run_whose_workflow_was_cancelled(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """A running run whose workflow was cancelled outside the run's own paths ends `failed`
    with the reason `workflow_cancelled`: its log is closed, its task token revoked and
    `run.finished` emitted once. A run whose workflow is alive is left running, and a
    second pass changes nothing."""
    from tumnis.modules.agents import workflows  # noqa: PLC0415

    world = await run_world(fake_runner, workspace, clock)
    async with relay(db):
        orphan = await _running(world, db, "Fix footer link")
        alive = await _running(world, db, "Fix header link")
        await dbos.cancel_workflow_async(str(orphan))

        ended = await workflows.reconcile_runs(datetime.now(UTC), None)
        again = await workflows.reconcile_runs(datetime.now(UTC), None)

    assert ended == [str(orphan)]
    assert again == []
    assert owner_rows(db, "SELECT status, stop_reason FROM runs WHERE id = %s", (orphan,)) == [
        ("failed", "workflow_cancelled")
    ]
    assert owner_rows(db, "SELECT status FROM runs WHERE id = %s", (alive,)) == [("running",)]
    assert owner_rows(
        db, "SELECT count(*) FROM task_tokens WHERE run_id = %s AND revoked_at IS NULL", (orphan,)
    ) == [(0,)]
    assert owner_rows(
        db,
        "SELECT count(*) FROM outbox WHERE name = 'run.finished' AND payload->>'run_id' = %s",
        (str(orphan),),
    ) == [(1,)]
    [(line,)] = owner_rows(
        db,
        "SELECT payload->>'text' FROM run_events WHERE run_id = %s ORDER BY seq DESC LIMIT 1",
        (orphan,),
    )
    assert line == "Stopped: the run's workflow ended"
