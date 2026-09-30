"""The run time caps (P2-04, SAF-5, R-29): the active-time cap and the wall-clock ceiling
are enforced inside `dispatch_run`, so a run that hits one stops its agent, ends
`timed_out` and keeps its log. Both take configurable durations here (R-30), on the real
clock.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from tumnis.modules.agents.tests.integration._runs import (
    cancels,
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


@pytest.fixture
def run_limits() -> Iterator[None]:
    """The plan's defaults come back after the test shortened them."""
    yield
    from tumnis.modules.agents import api as agents  # noqa: PLC0415

    agents.configure_runs()


def _run(db: DbUrls, run_id: UUID) -> tuple[str, str | None]:
    [(status, reason)] = owner_rows(
        db, "SELECT status, stop_reason FROM runs WHERE id = %s", (run_id,)
    )
    return status, reason


@pytest.mark.req("SAF-5")
@pytest.mark.wp("P2-04")
@pytest.mark.xfail(strict=True, reason="spec:P2-04")
async def test_run_times_out_at_limit_and_fails_cleanly(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    run_limits: None,
) -> None:
    """T-P2-04-04
    At the active-time cap (2 s here, the project's 60 minutes by default) the agent gets
    `cancel`, the run ends `timed_out` with reason `time_limit`, the task stays
    `in_progress`, the log is kept with the system line "Stopped at the time limit" as its
    last event, and a `run_limit` review item names the run.
    """
    from tumnis.modules.agents import api as agents  # noqa: PLC0415

    agents.configure_runs(active_cap_seconds=2)
    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")
    async with relay(db):
        run_id = await world.request(task.id)
        world.runner.wait_for(lambda r: any(m.run_id == run_id for m in r.runs()))
        stream(world.runner, run_id, 1, "Checking out the branch")
        assert await wait_until(lambda: _run(db, run_id)[0] == "timed_out", timeout=15)

    assert _run(db, run_id) == ("timed_out", "time_limit")
    world.runner.wait_for(lambda r: bool(cancels(r, run_id)))
    assert len(cancels(world.runner, run_id)) == 1
    assert owner_rows(db, "SELECT status::text FROM tasks WHERE id = %s", (task.id,)) == [
        ("in_progress",)
    ]
    events = owner_rows(
        db,
        "SELECT kind, payload FROM run_events WHERE run_id = %s ORDER BY seq",
        (run_id,),
    )
    assert any(p.get("text") == "Checking out the branch" for _k, p in events)
    last_kind, last = events[-1]
    assert last_kind == "log"
    assert last["text"] == "Stopped at the time limit"
    items = owner_rows(
        db,
        "SELECT target_type, target_id FROM review_items WHERE kind = 'run_limit'",
    )
    assert items == [("run", run_id)]
    assert owner_rows(
        db,
        "SELECT count(*) FROM outbox WHERE name = 'run.finished' AND payload->>'run_id' = %s",
        (str(run_id),),
    ) == [(1,)]


@pytest.mark.req("SAF-5")
@pytest.mark.wp("P2-04")
@pytest.mark.xfail(strict=True, reason="spec:P2-04")
async def test_wall_clock_ceiling_ends_waiting_run(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    run_limits: None,
) -> None:
    """T-P2-04-18
    A run parked on a human (waiting time does not count against the active cap) still
    ends at the wall-clock ceiling (3 s here, 24 h by default): `timed_out` with reason
    `wall_clock_ceiling`, and its agent gets `cancel`.
    """
    from tumnis.modules.agents import api as agents  # noqa: PLC0415

    agents.configure_runs(wall_clock_ceiling_seconds=3)
    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")
    async with relay(db):
        run_id = await world.request(task.id)
        world.runner.wait_for(lambda r: any(m.run_id == run_id for m in r.runs()))
        await agents.signal_run(world.ctx, run_id, "waiting")
        assert await wait_until(lambda: _run(db, run_id)[0] == "waiting_on_human")
        assert await wait_until(lambda: _run(db, run_id)[0] == "timed_out", timeout=15)

    assert _run(db, run_id) == ("timed_out", "wall_clock_ceiling")
    world.runner.wait_for(lambda r: bool(cancels(r, run_id)))
