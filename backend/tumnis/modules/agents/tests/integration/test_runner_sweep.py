"""The runner sweep (P1-04, FR-5.11, FR-5.9, A9): three missed heartbeats on the clock
mark a runner offline, and a run waiting on it ends `runner_lost`."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._pg import OWNER

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


def _owner(db: DbUrls, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        return conn.execute(query.encode(), params).fetchall()


@pytest.mark.req("FR-5.11", "FR-5.9")
@pytest.mark.wp("P1-04")
async def test_sweep_marks_offline_and_fails_inflight_runs(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-04-05
    Given the fake runner registered at clock T and a `run_skill` workflow waiting on it
    (the runner acked the run and never answers), when the clock moves to T + 46 s and
    `runner_sweep` runs, then `runners.status = 'offline'` and the workflow receives
    `{status: "runner_lost"}` through DBOS.send: it returns `runner_lost` and the run row
    says so. A sweep at T + 45 s changes nothing.
    """
    from tumnis.modules.agents import workflows  # noqa: PLC0415
    from tumnis.modules.agents.tests.contract.base import make_packet  # noqa: PLC0415

    runner = fake_runner(profiles=["acme-site"])  # nothing scripted: acks, never answers
    profile_id = fake_runner.register_profile("acme-site", runner=runner)
    packet = make_packet(profile_id)
    handle = await workflows.start_run_skill(workspace.id, packet)
    await asyncio.to_thread(
        runner.wait_for, lambda r: any(run.run_id == packet.run_id for run in r.runs())
    )

    clock.advance(timedelta(seconds=45))
    assert await workflows.runner_sweep(clock.now(), None) == []
    status = _owner(db, "SELECT status FROM runners WHERE id = %s", (runner.runner_id,))
    assert status == [("online",)]

    clock.advance(timedelta(seconds=1))
    lost = await workflows.runner_sweep(clock.now(), None)
    assert lost == [str(packet.run_id)]
    status = _owner(db, "SELECT status FROM runners WHERE id = %s", (runner.runner_id,))
    assert status == [("offline",)]

    outcome = await asyncio.wait_for(handle.get_result(), 20)
    assert outcome["status"] == "runner_lost"
    assert outcome["run_id"] == str(packet.run_id)
    rows = _owner(
        db, "SELECT status, finished_at IS NOT NULL FROM runs WHERE id = %s", (packet.run_id,)
    )
    assert rows == [("runner_lost", True)]
