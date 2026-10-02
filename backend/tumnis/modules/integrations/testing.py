"""The `connector-sync-tick` test tick (P3-02, R-37; fakes only): `POST
/v1/test/tick/connector-sync-tick` enqueues one `connector_sync_tick` run now, as the
every-minute schedule would. Registered at import; the router imports this module so the
api process has it."""

from datetime import datetime
from typing import Any, Final

from tumnis.core.ticks import register_tick

TICK_NAME: Final = "connector-sync-tick"  # workflows.TICK_SCHEDULE_NAME
QUEUE: Final = "sync"  # api.SYNC_QUEUE
WORKFLOW: Final = "integrations_connector_sync_tick"  # api.TICK_WORKFLOW


async def tick(client: Any, now: datetime) -> int:
    """Enqueue one sync tick through `client` (a DBOSClient); one workflow started."""
    await client.enqueue_async({"queue_name": QUEUE, "workflow_name": WORKFLOW}, now, None)
    return 1


register_tick(TICK_NAME, tick)
