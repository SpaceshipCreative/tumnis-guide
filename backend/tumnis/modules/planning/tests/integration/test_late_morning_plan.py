"""A morning plan built late (P1-11, FR-4.3): the first tick after an outage, or a plan
time after the working hours start, never places a block before the build's clock.

Monday 2026-03-09 in New York (the `workspace` fixture), default hours 09:00 to 18:00, no
events; the master is offline, so the plan is the due-date fallback.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.planning.tests.integration._plan import (
    MASTER,
    MONDAY,
    build,
    master_on,
    new_project,
    new_task,
    plan_items,
    published,
)

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

LATE = datetime(2026, 3, 9, 15, 2, tzinfo=UTC)  # 11:02 in New York, hours began at 09:00


@pytest.mark.req("FR-4.3")
@pytest.mark.wp("P1-11")
async def test_late_morning_plan_places_no_block_before_now(
    dbos: Any,
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    project = await new_project(workspace, clock, "Acme site")
    for n, minutes in enumerate((60, 45, 30)):
        await new_task(
            workspace, clock, project, f"Human {n}", label="human", estimate_minutes=minutes
        )
    master_on(fake_runner).offline(MASTER)

    plan_id = await build(workspace.id, MONDAY, "morning", LATE)

    plan = published(db)
    assert plan is not None
    assert plan["id"] == plan_id
    blocks = [i for i in plan_items(db, plan_id) if i["block_start"] is not None]
    assert len(blocks) == 3
    # Blocks start on the next 5-minute mark at or after the build's clock.
    assert min(i["block_start"] for i in blocks) == datetime(2026, 3, 9, 15, 5, tzinfo=UTC)
    assert all(i["block_start"] >= LATE for i in blocks)
