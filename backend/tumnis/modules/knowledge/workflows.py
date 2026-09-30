"""knowledge DBOS workflows and steps (P1-15).

- `knowledge_folder_sync(workspace_id, location_id)` on the `sync` queue (FR-15.12): the
  location's health (queued note writes drained when it answers), then one plan step (the
  decisions, recorded by DBOS) and one step per decision; after decision n is applied,
  kill point `knowledge.folder_sync.applied_<n>`. A resumed sync replays the recorded plan
  and applies only what is left. Then the extraction requests and `last_sync_at`.
- `knowledge_folder_sync_tick`: every 15 minutes (plan default, A9 folder scans), one sync
  per live location of every workspace, deduplicated per location: a sync still queued
  for a location is not queued twice.
- `local_watch(stop)`: run by the worker beside the relay. It watches every local-disk
  server path with watchfiles and queues a sync of a location once its changes settle.
  Events are only a hint to sync sooner: a sync always compares everything, so a missed
  event is harmless, and the 15-minute tick is the backstop.

The engine itself is `knowledge.sync` (net policy, clock and extraction hook there).
"""

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime
from typing import Any, Final

from dbos import DBOS, SetEnqueueOptions, SetWorkflowID

from tumnis.core import faults
from tumnis.modules.knowledge import sync

log = logging.getLogger(__name__)

SYNC_QUEUE: Final = "sync"  # A9, registered by the worker
FOLDER_SYNC_WORKFLOW: Final = "knowledge_folder_sync"
TICK_SCHEDULE_NAME: Final = "knowledge-folder-sync-tick"
TICK_EVERY_MINUTES: Final = 15  # plan default
TICK_SCHEDULE: Final = f"*/{TICK_EVERY_MINUTES} * * * *"
WATCH_DEBOUNCE_MS: Final = 5_000  # plan default (partial files from Syncthing and the like)
WATCH_REFRESH_S: Final = 300.0  # how often the watched locations are read again
WATCH_WAKE_MS: Final = 30_000  # awatch yields this often with no change (the refresh check)
STEP_RETRY: Final[dict[str, Any]] = {
    "retries_allowed": True,
    "max_attempts": 3,
    "interval_seconds": 0.1,
}


@DBOS.step(**STEP_RETRY)
async def sync_begin(workspace_id: str, location_id: str) -> str:
    return await sync.begin(workspace_id, location_id)


@DBOS.step(**STEP_RETRY)
async def sync_plan(workspace_id: str, location_id: str) -> list[dict[str, Any]]:
    return await sync.plan(workspace_id, location_id)


@DBOS.step(**STEP_RETRY)
async def sync_apply(workspace_id: str, location_id: str, item: dict[str, Any]) -> list[list[str]]:
    return await sync.apply(workspace_id, location_id, item)


@DBOS.step(**STEP_RETRY)
async def sync_extract(workspace_id: str, requests: list[list[str]]) -> None:
    await sync.request_extraction(workspace_id, requests)


@DBOS.step(**STEP_RETRY)
async def sync_finish(workspace_id: str, location_id: str) -> None:
    await sync.finish(workspace_id, location_id)


@DBOS.workflow(name=FOLDER_SYNC_WORKFLOW)
async def folder_sync(workspace_id: str, location_id: str) -> dict[str, Any]:
    """One full comparison of the location's project folders with their records."""
    status = await sync_begin(workspace_id, location_id)
    if status != "online":
        return {"status": status, "applied": 0}
    items = await sync_plan(workspace_id, location_id)
    requests: list[list[str]] = []
    for n, item in enumerate(items, start=1):
        requests += await sync_apply(workspace_id, location_id, item)
        faults.killpoint(f"knowledge.folder_sync.applied_{n}")
    if requests:
        await sync_extract(workspace_id, requests)
    await sync_finish(workspace_id, location_id)
    return {"status": status, "applied": len(items)}


async def enqueue_folder_sync(workspace_id: str, location_id: str, *, workflow_id: str) -> None:
    """Queue a sync of the location unless one is queued already (DBOS deduplication on the
    queue: the queued one is kept)."""
    with (
        SetWorkflowID(workflow_id),
        SetEnqueueOptions(
            deduplication_id=f"folder-sync:{location_id}", duplication_policy="return-existing"
        ),
    ):
        await DBOS.enqueue_workflow_async(SYNC_QUEUE, folder_sync, workspace_id, location_id)


@DBOS.step()
async def sync_locations() -> list[tuple[str, str]]:
    return await sync.locations()


def schedules() -> list[Any]:
    """This module's DBOS schedules, applied by the worker after launch."""
    return [
        {
            "schedule_name": TICK_SCHEDULE_NAME,
            "workflow_fn": sync_tick,
            "schedule": TICK_SCHEDULE,
            "queue_name": SYNC_QUEUE,
        }
    ]


@DBOS.workflow(name="knowledge_folder_sync_tick")
async def sync_tick(scheduled_at: datetime, context: Any) -> None:
    """Scheduled every 15 minutes on the sync queue: one sync per live location, with a
    workflow id per (location, tick) so a replayed tick starts none twice."""
    for workspace_id, location_id in await sync_locations():
        await enqueue_folder_sync(
            workspace_id,
            location_id,
            workflow_id=f"folder-sync:{location_id}:{scheduled_at.isoformat()}",
        )


async def local_watch(stop: asyncio.Event) -> None:
    """Watch every local-disk server path until `stop`: a change inside a project folder
    queues a sync of its location. The watched roots are read again every 5 minutes; a
    watcher that fails waits, then starts again."""
    from watchfiles import awatch  # noqa: PLC0415  # only the worker watches files

    while not stop.is_set():
        try:
            roots = [
                found
                for found in await sync.watched_roots()
                if await asyncio.to_thread(os.path.isdir, found[2])  # unmounted: the tick's
            ]
            if not roots:
                await _sleep_until(stop, WATCH_REFRESH_S)
                continue
            refresh_at = time.monotonic() + WATCH_REFRESH_S
            async for changes in awatch(
                *{root for _ws, _loc, root in roots},
                stop_event=stop,
                debounce=WATCH_DEBOUNCE_MS,
                rust_timeout=WATCH_WAKE_MS,
                yield_on_timeout=True,
            ):
                touched = {sync.watched_location(path, roots) for _change, path in changes}
                for workspace_id, location_id in sorted(t for t in touched if t is not None):
                    await enqueue_folder_sync(
                        workspace_id,
                        location_id,
                        workflow_id=f"folder-sync:{location_id}:watch:{uuid.uuid4()}",
                    )
                if time.monotonic() >= refresh_at:
                    break
        except Exception:  # a failing watcher must not stop the worker
            log.exception("local folder watch failed; starting again")
            await _sleep_until(stop, WATCH_REFRESH_S)


async def _sleep_until(stop: asyncio.Event, seconds: float) -> None:
    """Wait `seconds`, or less when `stop` is set."""
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        return
