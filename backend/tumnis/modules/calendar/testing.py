"""The `calendar-sync` test tick (R-37, fakes only; A1.3): `POST /v1/test/tick/calendar-sync`
does what one run of the scheduled `calendar_sync_tick` does, now: it enqueues
`calendar_connector_sync` on the `sync` queue for every connected Google account of every
workspace, then waits, at most TICK_WAIT_S, for those syncs to end, so a journey reads the
synced events (and the free blocks they leave) as soon as the tick answers. A sync that
ends `busy` (the scheduled sync held the account) is enqueued again, BUSY_RETRIES times at
most. A sync still running at TICK_WAIT_S, or still `busy`, answers 503 `sync_incomplete`
instead of the count. Each call gets fresh workflow ids: a test fires it to sync again, whatever the
server clock says. Registered at import; the router imports this module so the api process
has it (the workflow is named, not imported: the api process never imports workflows)."""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from tumnis.core import audit, db
from tumnis.core.errors import ProblemError
from tumnis.core.tenancy import WorkspaceContext
from tumnis.core.ticks import register_tick
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.calendar import api

_log = logging.getLogger(__name__)

TICK_NAME: Final = "calendar-sync"
TICK_WAIT_S: float = 8.0  # under the e2e request timeout (10 s); read per call
BUSY_RETRIES: Final = 3
BUSY_PAUSE_S: float = 0.25


async def _connections() -> list[tuple[UUID, UUID]]:
    """(workspace, connection) of every connected Google account."""
    async with db.app_sessionmaker()() as s, s.begin():
        workspace_ids = list(await audit.workspace_ids(s))
    found: list[tuple[UUID, UUID]] = []
    for workspace_id in workspace_ids:
        ctx = WorkspaceContext(workspace_id, SYSTEM_ACTOR)
        found += [(workspace_id, conn) for conn in await api.connected_accounts(ctx)]
    return found


async def _sync(client: Any, workspace_id: UUID, connection_id: UUID, now: datetime) -> str:
    """One account's sync, enqueued again while it ends `busy`; how it ended."""
    status = "busy"
    for _ in range(BUSY_RETRIES + 1):
        options = {
            "queue_name": api.SYNC_QUEUE,
            "workflow_name": api.SYNC_WORKFLOW,
            "workflow_id": f"calendar-sync:{connection_id}:test:{now.isoformat()}:{uuid.uuid4()}",
        }
        handle = await client.enqueue_async(options, str(workspace_id), str(connection_id))
        result: dict[str, Any] = await handle.get_result()
        status = str(result.get("status"))
        if status != "busy":
            break
        await asyncio.sleep(BUSY_PAUSE_S)
    return status


async def tick(client: Any, now: datetime) -> int:
    """Sync every connected account through `client` (a DBOSClient) and wait for the
    syncs (see the module); how many accounts it synced. 503 `sync_incomplete` when a sync
    is still running after TICK_WAIT_S or still `busy` after its retries, so a journey
    fails here rather than reading events that have not landed."""
    connections = await _connections()
    if not connections:
        return 0
    syncs = asyncio.gather(*(_sync(client, ws, conn, now) for ws, conn in connections))
    try:
        statuses = await asyncio.wait_for(syncs, TICK_WAIT_S)
    except TimeoutError:
        _log.warning("calendar sync tick: sync(s) still running after %s s", TICK_WAIT_S)
        raise ProblemError(
            503,
            "sync_incomplete",
            f"calendar sync still running after {TICK_WAIT_S} s",
        ) from None
    busy = statuses.count("busy")
    if busy:
        raise ProblemError(
            503,
            "sync_incomplete",
            f"{busy} calendar sync(s) still busy after {BUSY_RETRIES} retries",
        )
    return len(connections)


register_tick(TICK_NAME, tick)
