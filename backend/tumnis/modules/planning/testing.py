"""The `planner-tick` test tick (SEED, R-37; fakes only): `POST /v1/test/tick/planner-tick`
does what one run of the scheduled `planner_tick` does, at the server clock's time: it
enqueues `build_plan` (trigger `morning`) for every workspace whose morning plan is due
(`api.due_plan_day`), under the same workflow id, so a plan is built once however often it
fires. It then waits, at most TICK_WAIT_S, for those builds to end, so a journey reads the
published plan as soon as the tick answers; a build still waiting on its agent then goes
on in the worker. Registered at import; the router imports this module so the api process
has it (the workflow is named, not imported: the api process never imports workflows)."""

import asyncio
import logging
from datetime import datetime
from typing import Any, Final

from tumnis.core import audit, db
from tumnis.core.tenancy import WorkspaceContext
from tumnis.core.ticks import register_tick
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.planning import api

_log = logging.getLogger(__name__)

TICK_NAME: Final = "planner-tick"  # workflows.PLANNER_TICK_NAME
TICK_WAIT_S: float = 25.0  # how long the tick waits for the builds it started (read per call)
TRIGGER: Final = "morning"


async def tick(client: Any, now: datetime) -> int:
    """Enqueue the due morning builds through `client` (a DBOSClient) and wait for them
    (see the module); how many it started."""
    async with db.app_sessionmaker()() as s, s.begin():
        workspace_ids = list(await audit.workspace_ids(s))
    handles = []
    for workspace_id in workspace_ids:
        day = await api.due_plan_day(WorkspaceContext(workspace_id, SYSTEM_ACTOR), now)
        if day is None:
            continue
        options = {
            "queue_name": api.MAINTENANCE_QUEUE,
            "workflow_name": api.BUILD_PLAN,
            "workflow_id": api.plan_workflow_id(workspace_id, day, TRIGGER),
        }
        handles.append(
            await client.enqueue_async(
                options, str(workspace_id), day.isoformat(), TRIGGER, now.isoformat()
            )
        )
    if handles:
        try:
            await asyncio.wait_for(asyncio.gather(*(h.get_result() for h in handles)), TICK_WAIT_S)
        except TimeoutError:
            _log.info("planner tick: %d build(s) still running", len(handles))
    return len(handles)


register_tick(TICK_NAME, tick)
