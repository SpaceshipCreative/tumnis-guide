"""The `overnight-release` test tick (P4-04, R-37; fakes only): `POST
/v1/test/tick/overnight-release` runs the morning release at the server clock's time in the
api process, the work the scheduled `release_overnight` workflow does: each workspace's due
overnight rows go out as one summary (its `notification.ready` in the same transaction),
and its browser push is enqueued through the app's DBOS client with the id `start_push`
uses. Registered at import; the router imports this module so the api process has it."""

from datetime import datetime
from typing import Any, Final

from tumnis.core.tenancy import WorkspaceContext
from tumnis.core.ticks import register_tick
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.notifications import api, workflows

TICK_NAME: Final = "overnight-release"


async def release(client: Any, now: datetime) -> int:
    """One morning release at `now`; how many summaries it sent."""
    released = 0
    for workspace_id in await api.overnight_workspaces():
        made = await api.release_overnight(WorkspaceContext(workspace_id, SYSTEM_ACTOR), now=now)
        if made is None:
            continue
        await client.enqueue_async(
            workflows.push_options(made), str(workspace_id), str(made), workflows.push_delays()
        )
        released += 1
    return released


register_tick(TICK_NAME, release)
