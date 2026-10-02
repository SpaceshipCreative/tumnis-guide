"""knowledge DBOS workflows and steps (P1-15 folder sync; P1-16 extraction).

Folder sync (P1-15):

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

The sync engine itself is `knowledge.sync` (net policy, clock and extraction hook there).
Every version the sync makes from folder bytes is `pending_scan` and goes to
`knowledge_extract_document` with `source = "storage"` (`enqueue_folder_extraction`, the
sync's default hook), so nothing found in a folder is served unscanned (#99).

Extraction (P1-16, SEC-10, FR-15.2, ADR-0007):

`knowledge_extract_document(workspace_id, version_id, source)` on the `extract` queue (which
only `worker-extract` dequeues; workflow id `extract:<version_id>`):

1. read the file into scratch, hashing it;
2. scan it with clamd: an infected file is quarantined and the workflow ends;
3. sniff its type from the content: a refused type or size ends in `failed` with the
   refusal's code, before anything is placed or converted;
3b. (uploads) place it at `uploads/<name>` in the project folder;
4. convert it with the extractor;
5. read each low-confidence PDF page with the vision model (a failed vision pass is skipped:
   the standard chunks stay);
6. to 9. store the document, chunk it, index the chunks, and mark it ready with its event.

Each step is a DBOS step calling `pipeline.<step>` by attribute at run time, so a killed
worker's replacement resumes at the step that had not finished, and a test can wrap a step.
Arguments and results are small JSON values; large outputs live in `extraction_artifacts`.
Any other final failure of a step ends the workflow in `failed` (`extraction_failed`).
"""

import asyncio
import contextvars
import logging
import os
import time
import uuid
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from dbos import DBOS, SetEnqueueOptions, SetWorkflowID
from dbos._error import DBOSMaxStepRetriesExceeded  # documented, not re-exported (3.1.0)

from tumnis.core import faults
from tumnis.core.tenancy import WorkspaceContext
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.knowledge import api, embeddings, move, pipeline, sync
from tumnis.modules.knowledge.rules import EMBED_BATCH, EMBED_QUEUE, REEMBED_WORKFLOW

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


# Extraction (P1-16)

WORKFLOW: Final = api.EXTRACT_WORKFLOW
EXTRACT_STEP_RETRY: Final[dict[str, Any]] = {
    "retries_allowed": True,
    "max_attempts": 3,
    "interval_seconds": 1,
    "backoff_rate": 2,
}
# clamd may be restarting or still loading its signatures: five tries over about a minute.
SCAN_RETRY: Final[dict[str, Any]] = {
    "retries_allowed": True,
    "max_attempts": 5,
    "interval_seconds": 5,
    "backoff_rate": 2,
}
FAILED: Final = "extraction_failed"


@DBOS.step(name="knowledge_extract_read", **EXTRACT_STEP_RETRY)
async def read_step(workspace_id: str, version_id: str, source: str) -> pipeline.Ref:
    return await pipeline.read(workspace_id, version_id, source)  # type: ignore[arg-type]


@DBOS.step(name="knowledge_extract_scan", **SCAN_RETRY)
async def scan_step(workspace_id: str, version_id: str, ref: pipeline.Ref) -> dict[str, Any]:
    return await pipeline.scan(workspace_id, version_id, ref)


@DBOS.step(name="knowledge_extract_quarantine", **EXTRACT_STEP_RETRY)
async def quarantine_step(workspace_id: str, version_id: str, signature: str, source: str) -> None:
    await pipeline.quarantine(workspace_id, version_id, signature, source)  # type: ignore[arg-type]


@DBOS.step(name="knowledge_extract_sniff", **EXTRACT_STEP_RETRY)
async def sniff_step(workspace_id: str, version_id: str, ref: pipeline.Ref) -> dict[str, Any]:
    return await pipeline.sniff(workspace_id, version_id, ref)


@DBOS.step(name="knowledge_extract_place", **EXTRACT_STEP_RETRY)
async def place_step(workspace_id: str, version_id: str, ref: pipeline.Ref) -> str:
    return await pipeline.place(workspace_id, version_id, ref)


@DBOS.step(name="knowledge_extract_convert", **EXTRACT_STEP_RETRY)
async def convert_step(
    workspace_id: str, version_id: str, ref: pipeline.Ref, kind: str
) -> dict[str, Any]:
    return await pipeline.convert(workspace_id, version_id, ref, kind)


@DBOS.step(name="knowledge_extract_vlm", **EXTRACT_STEP_RETRY)
async def vlm_step(
    workspace_id: str, version_id: str, ref: pipeline.Ref, low_pages: list[int]
) -> None:
    await pipeline.vlm(workspace_id, version_id, ref, low_pages)


@DBOS.step(name="knowledge_extract_store", **EXTRACT_STEP_RETRY)
async def store_step(workspace_id: str, version_id: str) -> None:
    await pipeline.store(workspace_id, version_id)


@DBOS.step(name="knowledge_extract_chunk", **EXTRACT_STEP_RETRY)
async def chunk_step(workspace_id: str, version_id: str) -> int:
    return await pipeline.chunk(workspace_id, version_id)


@DBOS.step(name="knowledge_extract_index", **EXTRACT_STEP_RETRY)
async def index_step(workspace_id: str, version_id: str) -> int:
    return await pipeline.index(workspace_id, version_id)


@DBOS.step(name="knowledge_extract_emit", **EXTRACT_STEP_RETRY)
async def emit_step(workspace_id: str, version_id: str) -> str:
    return await pipeline.emit(workspace_id, version_id)


@DBOS.step(name="knowledge_extract_fail", **EXTRACT_STEP_RETRY)
async def fail_step(workspace_id: str, version_id: str, code: str) -> None:
    await pipeline.fail(workspace_id, version_id, code)


async def _extract(workspace_id: str, version_id: str, source: str) -> str:
    ref = await read_step(workspace_id, version_id, source)
    scanned = await scan_step(workspace_id, version_id, ref)
    if scanned["infected"]:
        await quarantine_step(workspace_id, version_id, scanned["signature"] or "unknown", source)
        return "quarantined"
    sniffed = await sniff_step(workspace_id, version_id, ref)
    if sniffed["refusal"] is not None:
        await fail_step(workspace_id, version_id, sniffed["refusal"])
        return "failed"
    if source == "spool":
        await place_step(workspace_id, version_id, ref)
    converted = await convert_step(workspace_id, version_id, ref, sniffed["kind"])
    faults.killpoint("extract.vlm_step")
    try:
        await vlm_step(workspace_id, version_id, ref, converted["low_pages"])
    except Exception:  # the vision pass is an improvement, never a gate
        DBOS.logger.warning("vision pass failed for %s; keeping the standard chunks", version_id)
    await store_step(workspace_id, version_id)
    await chunk_step(workspace_id, version_id)
    await index_step(workspace_id, version_id)
    await emit_step(workspace_id, version_id)
    return "ready"


@DBOS.workflow(name=WORKFLOW)
async def extract_document(workspace_id: str, version_id: str, source: str) -> str:
    try:
        return await _extract(workspace_id, version_id, source)
    except Exception:  # a step failed for good: the file is failed, not lost
        DBOS.logger.exception("extraction of %s failed", version_id)
        await fail_step(workspace_id, version_id, FAILED)
        return "failed"


async def enqueue_folder_extraction(workspace_id: UUID, version_id: UUID, _path: str) -> None:
    """The folder sync's extraction of a version made from folder bytes: P1-16's pipeline
    with `source = "storage"` (scanned where the file lies, never placed again), on the
    `extract` queue under the id the api uses, `extract:<version_id>`, so a repeat returns
    the workflow already there (DBOS "Workflow IDs and Idempotency").

    The sync calls this inside a DBOS step, and DBOS refuses to start a workflow from a
    step, so the enqueue runs in a fresh context, as `agents.workflows.start_provision`
    does; a re-run step enqueues the same id again, which is a no-op."""

    async def enqueue() -> None:
        with SetWorkflowID(f"extract:{version_id}"):
            await DBOS.enqueue_workflow_async(
                api.EXTRACT_QUEUE, extract_document, str(workspace_id), str(version_id), "storage"
            )

    await asyncio.get_running_loop().create_task(enqueue(), context=contextvars.Context())


sync.register_extraction(enqueue_folder_extraction)


# Re-embedding on a model change (P3-10, FR-11.10, REL-3)

REEMBED_INDEX_POLL_S: Final = 60.0  # how often a build waits for the model's HNSW index
REEMBED_INDEX_POLLS: Final = 1440  # a day of waiting; then the model stays `building`
REEMBED_ROUNDS: Final = 3  # passes over chunks added while the last batches ran


def _workspace(workspace_id: str) -> WorkspaceContext:
    return WorkspaceContext(UUID(workspace_id), SYSTEM_ACTOR)


@DBOS.step(name="knowledge_reembed_index_ready", **STEP_RETRY)
async def reembed_index_step(workspace_id: str, model: str) -> bool:
    return await embeddings.reembed_index_ready(_workspace(workspace_id), model)


@DBOS.step(name="knowledge_reembed_batch", **EXTRACT_STEP_RETRY)
async def reembed_batch_step(workspace_id: str, model: str) -> int:
    return await embeddings.reembed_batch(_workspace(workspace_id), model)


@DBOS.step(name="knowledge_reembed_finish", **STEP_RETRY)
async def reembed_finish_step(workspace_id: str, model: str, replaces: str | None) -> int:
    return await embeddings.reembed_finish(_workspace(workspace_id), model, replaces)


@DBOS.workflow(name=REEMBED_WORKFLOW)
async def reembed_all(workspace_id: str, model: str, replaces: str | None) -> dict[str, Any]:
    """`knowledge_reembed_all` on the `embed` queue (workflow id `reembed:<ws>:<model>`):
    once the model's partial HNSW index exists, embed every chunk lacking its vector, a
    step per EMBED_BATCH chunks (kill point `knowledge.reembed.batch_<n>` after step n,
    so a killed worker's replacement resumes after the last finished batch and no chunk
    is sent twice), then switch: the model `active`, `replaces` `retired` and its rows
    deleted. Search keeps using the active model until that switch."""
    for _ in range(REEMBED_INDEX_POLLS):
        if await reembed_index_step(workspace_id, model):
            break
        await DBOS.sleep_async(REEMBED_INDEX_POLL_S)
    else:
        return {"status": "no_index", "embedded": 0}
    batches = embedded = 0
    for _ in range(REEMBED_ROUNDS):
        while True:
            count = await reembed_batch_step(workspace_id, model)
            batches += 1
            embedded += count
            faults.killpoint(f"knowledge.reembed.batch_{batches}")
            if count < EMBED_BATCH:
                break
        if await reembed_finish_step(workspace_id, model, replaces) == 0:
            return {"status": "active", "embedded": embedded}
    return {"status": "incomplete", "embedded": embedded}


async def enqueue_reembed(
    workflow_id: str, workspace_id: str, model: str, replaces: str | None
) -> None:
    """`reembed_all` on the `embed` queue in this process (`embeddings.start_reembed`),
    under its id, so a repeat returns the workflow already there. Started in a fresh
    context, as `enqueue_folder_extraction` is, so a caller inside a step can use it."""

    async def enqueue() -> None:
        with SetWorkflowID(workflow_id):
            await DBOS.enqueue_workflow_async(
                EMBED_QUEUE, reembed_all, workspace_id, model, replaces
            )

    await asyncio.get_running_loop().create_task(enqueue(), context=contextvars.Context())


embeddings.register_reembed_starter(enqueue_reembed)


# --- Moving a project folder (P3-14, FR-15.12, REL-3) ---------------------------------------

MOVE_WORKFLOW: Final = "knowledge_move_project_folder"
_MOVE_IDS: Final = uuid.UUID("5d0c0a43-6a4e-4c55-9a3e-6d6f76650000")  # uuid5 namespace


@DBOS.step(**STEP_RETRY)
async def move_begin_step(
    workspace_id: str, project_id: str, to_location: str, to_path: str, move_id: str
) -> dict[str, Any]:
    return await move.begin(workspace_id, project_id, to_location, to_path, move_id=UUID(move_id))


@DBOS.step(**STEP_RETRY)
async def move_list_step(workspace_id: str, record: dict[str, Any]) -> list[list[str]]:
    return await move.list_source(workspace_id, record)


@DBOS.step(**STEP_RETRY)
async def move_copy_step(
    workspace_id: str, record: dict[str, Any], batch: list[list[str]]
) -> str | None:
    return await move.copy_batch(workspace_id, record, batch)


@DBOS.step(**STEP_RETRY)
async def move_verify_step(
    workspace_id: str, record: dict[str, Any], files: list[list[str]]
) -> dict[str, list[Any]] | None:
    return await move.verify(workspace_id, record, files)


@DBOS.step(**STEP_RETRY)
async def move_fail_step(workspace_id: str, move_id: str, reason: str) -> None:
    await move.fail(workspace_id, move_id, reason)


@DBOS.step(**STEP_RETRY)
async def move_switch_step(
    workspace_id: str,
    record: dict[str, Any],
    files: list[list[str]],
    stats: dict[str, list[Any]],
) -> str | None:
    return await move.switch(workspace_id, record, files, stats)


async def _copy_and_verify(
    workspace_id: str, record: dict[str, Any]
) -> tuple[list[list[str]], dict[str, list[Any]]] | str:
    """(the listed files, the copies' stats), or why the move fails."""
    try:
        files = await move_list_step(workspace_id, record)
        for n, start in enumerate(range(0, len(files), move.BATCH), start=1):
            conflict = await move_copy_step(workspace_id, record, files[start : start + move.BATCH])
            if conflict is not None:
                return conflict
            faults.killpoint(f"knowledge.move_project_folder.batch_{n}")
        stats = await move_verify_step(workspace_id, record, files)
    except DBOSMaxStepRetriesExceeded:
        log.exception("knowledge: the move of project %s could not copy", record["project_id"])
        return "copy_failed"
    if stats is None:
        return "hash_mismatch"
    return files, stats


@DBOS.workflow(name=MOVE_WORKFLOW)
async def move_project_folder(
    workspace_id: str, project_id: str, to_location: str, to_path: str
) -> dict[str, Any]:
    """Copy the project's folder to `to_location`/`to_path` in batches (kill point
    `knowledge.move_project_folder.batch_<n>` after batch n), verify every hash, switch.
    A resumed move copies only the batches left. The source is never changed. Once begun,
    a move never stays `copying`: a copy that cannot be made (`target_conflict`, or
    `copy_failed` once a step's retries run out), a hash mismatch, a source changed
    while copying (`changed_during_move`) or a switch whose retries run out
    (`switch_failed`) ends it `failed` with nothing switched."""
    move_id = str(uuid.uuid5(_MOVE_IDS, DBOS.workflow_id or str(uuid.uuid4())))
    record = await move_begin_step(workspace_id, project_id, to_location, to_path, move_id)
    if "error" in record:
        return {"status": "refused", "reason": record["error"]}
    copied = await _copy_and_verify(workspace_id, record)
    if isinstance(copied, str):
        await move_fail_step(workspace_id, record["move_id"], copied)
        return {"status": "failed", "reason": copied, "move_id": record["move_id"]}
    files, stats = copied
    try:
        failed = await move_switch_step(workspace_id, record, files, stats)
    except DBOSMaxStepRetriesExceeded:
        log.exception("knowledge: the move of project %s could not switch", record["project_id"])
        failed = "switch_failed"
        await move_fail_step(workspace_id, record["move_id"], failed)
    if failed is not None:
        return {"status": "failed", "reason": failed, "move_id": record["move_id"]}
    return {"status": "switched", "move_id": record["move_id"], "verified": len(stats)}


# P2-18: the folder steps of the project archive workflows register with projects.
from tumnis.modules.knowledge import archive as _archive  # noqa: E402, F401
