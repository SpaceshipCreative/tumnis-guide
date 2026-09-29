"""tasks DBOS workflows (P0-19, FR-3.5, FR-3.6, REL-3, REL-6).

- `day_close_tick` (schedule `day-close-tick`, every 5 minutes, UTC): for each workspace,
  when its local midnight has passed since the anchor (the later of its last close and its
  timezone change), enqueue `close_day` on the maintenance queue, deduplicated per
  workspace and day; then enqueue `recurrence_tick` for it.
- `close_day` runs `roll_over_today`, one transactional step: Today tasks back to Backlog
  and the `day_closes` row commit together or not at all, so a worker killed inside it
  reruns it once on recovery (kill point `tasks.roll_over_today`).
- `recurrence_tick` creates each rule's next instance once its latest one is overdue and
  still open (`create_successor`, idempotent through the occurrence index).
- `housekeeping` (schedule `housekeeping`, hourly at :17, maintenance queue): purge tasks
  trashed more than 30 days ago, then expire idempotency keys, each per workspace in
  batches until none is left.

Time is an argument: the scheduled time for the scheduled workflows, and `now` for the
children (read once through a step when a caller leaves it out), so a replay sees the same
instant.
"""

import contextlib
from datetime import date, datetime, timedelta
from typing import Any, Final
from uuid import UUID
from zoneinfo import ZoneInfo

from dbos import DBOS, SetEnqueueOptions, SetWorkflowID
from dbos._error import DBOSQueueDeduplicatedError  # dbos 3.1.0: not re-exported

from tumnis.core import audit, db, faults, idempotency
from tumnis.core.clock import Clock, SystemClock
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.core.workflows_ops import MAINTENANCE_QUEUE
from tumnis.modules.tasks import api
from tumnis.modules.tasks import rules_recurrence as rr

DAY_CLOSE_SCHEDULE_NAME: Final = "day-close-tick"
DAY_CLOSE_SCHEDULE: Final = "*/5 * * * *"  # UTC (A9)
HOUSEKEEPING_SCHEDULE_NAME: Final = "housekeeping"
HOUSEKEEPING_SCHEDULE: Final = "17 * * * *"  # UTC (A9)
TRASH_RETENTION: Final = timedelta(days=30)  # plan default
HOUSEKEEPING_BATCH: Final = 1_000  # plan default: rows deleted per batch
KILLPOINT: Final = "tasks.roll_over_today"

_clock: Clock = SystemClock()


def day_close_id(workspace_id: UUID | str, day: date) -> str:
    """The workflow and deduplication ID of one workspace's close of one local day."""
    return f"day_close:{workspace_id}:{day.isoformat()}"


def _ctx(workspace_id: UUID | str) -> WorkspaceContext:
    return WorkspaceContext(UUID(str(workspace_id)), SYSTEM_ACTOR)


@DBOS.step()
async def current_time() -> datetime:
    return _clock.now()


@DBOS.step()
async def list_workspaces() -> list[str]:
    async with db.app_sessionmaker()() as s, s.begin():
        return [str(workspace_id) for workspace_id in await audit.workspace_ids(s)]


@DBOS.step()
async def day_close_facts(workspace_id: str) -> tuple[str, datetime]:
    async with tenant_session(_ctx(workspace_id)) as s:
        facts = await api.day_close_facts(s)
    return facts.timezone, facts.anchor


async def _enqueue(workflow_id: str | None, fn: Any, *args: Any) -> None:
    """Enqueue on the maintenance queue; with an ID, once (a duplicate while the first is
    queued or running is refused by DBOS, and a finished one keeps its ID)."""
    if workflow_id is None:
        await DBOS.enqueue_workflow_async(MAINTENANCE_QUEUE, fn, *args)
        return
    with (
        SetWorkflowID(workflow_id),
        SetEnqueueOptions(deduplication_id=workflow_id),
        contextlib.suppress(DBOSQueueDeduplicatedError),
    ):
        await DBOS.enqueue_workflow_async(MAINTENANCE_QUEUE, fn, *args)


@DBOS.workflow()
async def day_close_tick(scheduled_time: datetime, context: Any) -> None:
    """Scheduled every 5 minutes; `scheduled_time` is the instant each workspace's local
    day end is judged against."""
    for workspace_id in await list_workspaces():
        zone, anchor = await day_close_facts(workspace_id)
        day = rr.day_close_due(anchor, scheduled_time, ZoneInfo(zone))
        if day is not None:
            await _enqueue(
                day_close_id(workspace_id, day), close_day, UUID(workspace_id), day, scheduled_time
            )
        await _enqueue(None, recurrence_tick, UUID(workspace_id), scheduled_time)


@DBOS.step()
async def roll_over_today(workspace_id: UUID, day: date, now: datetime) -> int:
    """One transaction: Today back to Backlog and the day_closes row (0 when the day was
    closed already)."""
    async with tenant_session(_ctx(workspace_id)) as s:
        rolled = await api.roll_over_today(s, day, now)
        faults.killpoint(KILLPOINT)  # after the UPDATEs, before the commit
    return rolled or 0


@DBOS.workflow()
async def close_day(workspace_id: UUID, day: date, now: datetime | None = None) -> int:
    """Closes local `day` for the workspace; returns how many tasks rolled over."""
    at = now if now is not None else await current_time()
    return await roll_over_today(workspace_id, day, at)


@DBOS.step()
async def create_successor(workspace_id: UUID, now: datetime) -> int:
    async with tenant_session(_ctx(workspace_id)) as s:
        return await api.create_due_successors(s, now)


@DBOS.workflow()
async def recurrence_tick(workspace_id: UUID, now: datetime | None = None) -> int:
    """Creates the due successors of the workspace's rules; returns how many."""
    at = now if now is not None else await current_time()
    return await create_successor(workspace_id, at)


@DBOS.step()
async def purge_trash(now: datetime) -> int:
    """Tasks trashed before `now` - 30 days, per workspace, in batches until none is left."""
    purged = 0
    for workspace_id in await _workspace_ids():
        while True:
            async with tenant_session(_ctx(workspace_id)) as s:
                batch = await api.purge_trash(s, now - TRASH_RETENTION, limit=HOUSEKEEPING_BATCH)
            purged += batch
            if batch == 0:
                break
    return purged


@DBOS.step()
async def expire_idempotency_keys(now: datetime) -> int:
    """Idempotency keys past `expires_at`, per workspace, in batches until none is left."""
    expired = 0
    for workspace_id in await _workspace_ids():
        while True:
            async with tenant_session(_ctx(workspace_id)) as s:
                batch = await idempotency.delete_expired(s, now, limit=HOUSEKEEPING_BATCH)
            expired += batch
            if batch == 0:
                break
    return expired


async def _workspace_ids() -> list[UUID]:
    async with db.app_sessionmaker()() as s, s.begin():
        return await audit.workspace_ids(s)


@DBOS.workflow()
async def housekeeping(scheduled_time: datetime, context: Any) -> None:
    """Scheduled hourly at :17 on the maintenance queue."""
    await purge_trash(scheduled_time)
    await expire_idempotency_keys(scheduled_time)
