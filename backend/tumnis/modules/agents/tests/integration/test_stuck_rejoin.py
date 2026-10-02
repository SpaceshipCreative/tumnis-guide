"""A later "Stuck" joins the stuck run already working (P4-02, FR-10.5; review follow-up).

The person taps "Stuck" on one check-in, and while the project agent's stuck run still
works on it, on the next check-in too. That second request starts no second run (one stuck
run per task), but it is not dropped either: it waits on the same run, so the step the
agent posts answers both, and the focus bar shows it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tumnis.modules.agents.tests.integration._runs import owner_rows, relay, run_world, wait_until
from tumnis.modules.agents.tests.integration._stuck import (
    call_tool,
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

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]


def _requests(db: DbUrls, task_id: UUID) -> list[tuple[object, ...]]:
    """(focus_event_id, run_id, state) of the task's stuck requests, oldest first."""
    return owner_rows(
        db,
        "SELECT focus_event_id, run_id, state FROM stuck_requests WHERE task_id = %s"
        " ORDER BY created_at",
        (task_id,),
    )


@pytest.mark.req("FR-10.5")
@pytest.mark.wp("P4-02")
async def test_second_check_in_waits_on_the_active_stuck_run(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    dbos_sys_db: DbUrls,
    session_client: SessionClient,
) -> None:
    world = await run_world(fake_runner, workspace, clock)
    task = await human_task(world)
    async with relay(db), quiet_stuck_workflows(dbos_sys_db):
        _, first = await tap_stuck(session_client, db, workspace.id, task.id, clock.now())
        assert await wait_until(lambda: len(stuck_runs(db, task.id)) == 1)
        [(run_id, _, _)] = stuck_runs(db, task.id)
        assert await wait_until(lambda: len(world.packets(run_id)) == 1)

        clock.advance(minutes=25)
        _, second = await tap_stuck(session_client, db, workspace.id, task.id, clock.now())
        assert await wait_until(lambda: len(_requests(db, task.id)) == 2)
        assert _requests(db, task.id) == [
            (first, run_id, "working"),
            (second, run_id, "working"),
        ]
        assert len(stuck_runs(db, task.id)) == 1

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

        async def split() -> bool:
            found = await next_step(session_client)
            return found is not None and found["state"] == "split"

        assert await wait_until(split)
        shown = await next_step(session_client)
    assert shown is not None
    assert shown["focus_event_id"] == str(second)
    assert [state for _, _, state in _requests(db, task.id)] == ["split", "split"]
