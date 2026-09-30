"""knowledge DBOS workflows and steps (P1-16, SEC-10, FR-15.2, ADR-0007).

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

from typing import Any, Final

from dbos import DBOS

from tumnis.core import faults
from tumnis.modules.knowledge import api, pipeline

WORKFLOW: Final = api.EXTRACT_WORKFLOW
STEP_RETRY: Final[dict[str, Any]] = {
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


@DBOS.step(name="knowledge_extract_read", **STEP_RETRY)
async def read_step(workspace_id: str, version_id: str, source: str) -> pipeline.Ref:
    return await pipeline.read(workspace_id, version_id, source)  # type: ignore[arg-type]


@DBOS.step(name="knowledge_extract_scan", **SCAN_RETRY)
async def scan_step(workspace_id: str, version_id: str, ref: pipeline.Ref) -> dict[str, Any]:
    return await pipeline.scan(workspace_id, version_id, ref)


@DBOS.step(name="knowledge_extract_quarantine", **STEP_RETRY)
async def quarantine_step(workspace_id: str, version_id: str, signature: str, source: str) -> None:
    await pipeline.quarantine(workspace_id, version_id, signature, source)  # type: ignore[arg-type]


@DBOS.step(name="knowledge_extract_sniff", **STEP_RETRY)
async def sniff_step(workspace_id: str, version_id: str, ref: pipeline.Ref) -> dict[str, Any]:
    return await pipeline.sniff(workspace_id, version_id, ref)


@DBOS.step(name="knowledge_extract_place", **STEP_RETRY)
async def place_step(workspace_id: str, version_id: str, ref: pipeline.Ref) -> str:
    return await pipeline.place(workspace_id, version_id, ref)


@DBOS.step(name="knowledge_extract_convert", **STEP_RETRY)
async def convert_step(
    workspace_id: str, version_id: str, ref: pipeline.Ref, kind: str
) -> dict[str, Any]:
    return await pipeline.convert(workspace_id, version_id, ref, kind)


@DBOS.step(name="knowledge_extract_vlm", **STEP_RETRY)
async def vlm_step(
    workspace_id: str, version_id: str, ref: pipeline.Ref, low_pages: list[int]
) -> None:
    await pipeline.vlm(workspace_id, version_id, ref, low_pages)


@DBOS.step(name="knowledge_extract_store", **STEP_RETRY)
async def store_step(workspace_id: str, version_id: str) -> None:
    await pipeline.store(workspace_id, version_id)


@DBOS.step(name="knowledge_extract_chunk", **STEP_RETRY)
async def chunk_step(workspace_id: str, version_id: str) -> int:
    return await pipeline.chunk(workspace_id, version_id)


@DBOS.step(name="knowledge_extract_index", **STEP_RETRY)
async def index_step(workspace_id: str, version_id: str) -> int:
    return await pipeline.index(workspace_id, version_id)


@DBOS.step(name="knowledge_extract_emit", **STEP_RETRY)
async def emit_step(workspace_id: str, version_id: str) -> str:
    return await pipeline.emit(workspace_id, version_id)


@DBOS.step(name="knowledge_extract_fail", **STEP_RETRY)
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
