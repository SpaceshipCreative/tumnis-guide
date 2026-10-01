"""Runaway limits (P2-09, SAF-5): a run that creates more tasks than its project allows
(default 20, subtasks included) is stopped and lands in review.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from tumnis.modules.agents.tests.integration._runs import (
    call_tool,
    cancels,
    owner_rows,
    relay,
    run_world,
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

TITLE = "Runaway task"


def _run(db: DbUrls, run_id: UUID) -> tuple[str, str | None, int]:
    [(status, reason, created)] = owner_rows(
        db, "SELECT status, stop_reason, tasks_created FROM runs WHERE id = %s", (run_id,)
    )
    return status, reason, created


@pytest.mark.req("SAF-5")
@pytest.mark.wp("P2-09")
async def test_twenty_first_task_stops_run_with_review_item(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P2-09-09
    The run's agent calls `create_task` 21 times with its task token, half of them as
    subtasks of its own task: the first 20 succeed, the 21st answers `run_limit_exceeded`
    and is not created. Exactly 20 tasks exist from the run (`runs.tasks_created` = 20);
    the run ends `cancelled` with `stop_reason` `tasks_per_run`, the runner gets a
    `cancel`, and one `run_limit` review item names the run.
    """
    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Split the launch work")
    async with relay(db):
        run_id = await world.request(task.id)
        world.runner.wait_for(lambda r: any(m.run_id == run_id for m in r.runs()))
        token = world.token(run_id)

        answers = []
        for n in range(21):
            args = {
                "project_id": str(world.project_id),
                "title": f"{TITLE} {n + 1}",
                "label": "human",
                "estimate_minutes": 20,
                "idempotency_key": f"runaway-{run_id}-{n}",
            }
            if n % 2:
                args["parent_id"] = str(task.id)
            answers.append(await call_tool(world.runner, token, "create_task", args))

        assert [a.code for a in answers[:20]] == [None] * 20, answers[:20]
        assert answers[20].code == "run_limit_exceeded", answers[20]

        assert await wait_until(lambda: _run(db, run_id)[0] == "cancelled", timeout=10)
        assert _run(db, run_id) == ("cancelled", "tasks_per_run", 20)
        assert len(cancels(world.runner, run_id)) == 1

    [(made,)] = owner_rows(db, "SELECT count(*) FROM tasks WHERE title LIKE %s", (f"{TITLE} %",))
    assert made == 20
    items = owner_rows(
        db,
        "SELECT target_type, target_id, payload->>'reason' FROM review_items"
        " WHERE kind = 'run_limit'",
    )
    assert items == [("run", run_id, "tasks_per_run")]
