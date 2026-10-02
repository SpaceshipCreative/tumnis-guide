"""The integrations test ticks (R-37; fakes only), registered at import; the router imports
this module so the api process has them (the workflows are named, not imported: the api
process never imports workflows).

- `connector-sync-tick` (P3-02): `POST /v1/test/tick/connector-sync-tick` enqueues one
  `connector_sync_tick` run now, as the every-minute schedule would.
- `retention-purge` (P3-09): `POST /v1/test/tick/retention-purge` enqueues one
  `retention_purge` run now, as the hourly housekeeping schedule would.
"""

from datetime import datetime
from typing import Any, Final

from tumnis.core.ticks import register_tick

TICK_NAME: Final = "connector-sync-tick"  # workflows.TICK_SCHEDULE_NAME
QUEUE: Final = "sync"  # api.SYNC_QUEUE
WORKFLOW: Final = "integrations_connector_sync_tick"  # api.TICK_WORKFLOW
RETENTION_TICK: Final = "retention-purge"  # workflows.RETENTION_SCHEDULE_NAME
RETENTION_QUEUE: Final = "maintenance"  # workflows_ops.MAINTENANCE_QUEUE
RETENTION_WORKFLOW: Final = "integrations_retention_purge"  # workflows.RETENTION_WORKFLOW


async def tick(client: Any, now: datetime) -> int:
    """Enqueue one sync tick through `client` (a DBOSClient); one workflow started."""
    await client.enqueue_async({"queue_name": QUEUE, "workflow_name": WORKFLOW}, now, None)
    return 1


async def retention_tick(client: Any, now: datetime) -> int:
    """Enqueue one retention purge run through `client`; one workflow started. No workflow
    id: each tick is a run of its own, as each scheduled hour is."""
    await client.enqueue_async(
        {"queue_name": RETENTION_QUEUE, "workflow_name": RETENTION_WORKFLOW}, now, None
    )
    return 1


register_tick(TICK_NAME, tick)
register_tick(RETENTION_TICK, retention_tick)
