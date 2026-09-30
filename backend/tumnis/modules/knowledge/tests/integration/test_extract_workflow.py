"""The `knowledge_extract_document` workflow (P1-16, FR-15.2, FR-15.12, ADR-0007): a kill
resumes at the failed step, low-confidence pages go to the vision model, a file found in a
project folder takes the same path without being placed again, and only the extract worker
dequeues the `extract` queue."""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import TYPE_CHECKING, Any

import pytest

from tests._pg import APP  # noqa: F401
from tests.fixtures import KILLED_EXIT, KILLER_APP_VERSION
from tumnis.core.ids import uuid7
from tumnis.core.net import NetPolicy
from tumnis.modules.knowledge.tests._samples import eicar, fixture_bytes
from tumnis.modules.knowledge.tests.integration._upload import (
    chunk_rows,
    rows,
    scalar,
    settled,
    upload,
)

if TYPE_CHECKING:
    from pathlib import Path

    from dbos import DBOS

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkerKillerFactory
    from tumnis.modules.knowledge.tests.integration.conftest import ExtractEnv

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

STEP_LOG = "tumnis.modules.knowledge.tests.integration._step_log"
QUEUE_PROBE = "tumnis.modules.knowledge.tests.integration._queue_probe"
PIPELINE = ["read", "scan", "sniff", "place", "convert", "vlm", "store", "chunk", "index", "emit"]


def _steps(log: Path) -> list[str]:
    return log.read_text().split() if log.exists() else []


@pytest.mark.req("FR-15.2")
@pytest.mark.wp("P1-16")
@pytest.mark.xfail(strict=True, reason="spec:P1-16")
async def test_kill_mid_pipeline_resumes_at_failed_step(
    extract_env: ExtractEnv,
    worker_killer: WorkerKillerFactory,
    db: DbUrls,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-P1-16-07
    An extract worker killed at the start of the vision step (after steps 1 to 4) is
    replaced by one that recovers the workflow: steps 1 to 4 are not run again, steps 5 to
    9 run once, the document is `ready` with its chunks, and exactly one `document.added`
    was emitted. The extractor is the fake with the rate card's stored conversion.
    """
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    env = extract_env
    log = tmp_path / "steps.log"
    monkeypatch.setenv("KNOWLEDGE_STEP_LOG", str(log))
    data = fixture_bytes("rate-card-table.pdf")
    version_id = uuid7()
    accepted = await knowledge.begin_upload(
        env.ws.ctx,
        project_id=env.project_id,
        name="rate-card-table.pdf",
        title=None,
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        version_id=version_id,
    )
    (env.dirs.spool / str(version_id)).write_bytes(data)
    killer = worker_killer("extract.vlm_step", events=0, imports=(STEP_LOG,), queues=("extract",))
    workflow_id = f"extract:{version_id}"

    code = await killer.enqueue_until_killed(
        queue_name="extract",
        workflow_name="knowledge_extract_document",
        workflow_id=workflow_id,
        args=(str(env.ws.id), str(version_id), "spool"),
    )

    assert code == KILLED_EXIT
    assert _steps(log) == ["read", "scan", "sniff", "place", "convert"]
    assert await killer.restart_until_done(workflow_id) == "SUCCESS"
    assert _steps(log) == PIPELINE
    assert scalar(db, "SELECT status FROM documents WHERE id = %s", accepted.id) == "ready"
    assert len(chunk_rows(db, str(accepted.id))) == 3
    assert scalar(db, "SELECT count(*) FROM outbox WHERE name = 'document.added'") == 1


@pytest.mark.req("FR-15.2")
@pytest.mark.wp("P1-16")
@pytest.mark.xfail(strict=True, reason="spec:P1-16")
async def test_low_confidence_pages_go_to_vlm_fake(
    extract_env: ExtractEnv, dbos: type[DBOS], session_client: SessionClient, db: DbUrls
) -> None:
    """T-P1-16-08
    `handwriting.pdf` page 2 is graded poor: the vision fake is asked for page 2 only, and
    page 2's chunks are the fake's Markdown (extractor `vlm`) while page 1 keeps Docling's.
    """
    env = extract_env
    env.vision.script(pages={2: "# Notes\n\nCall Sam on Friday about the launch."})
    response = await upload(
        session_client, env.project_id, "handwriting.pdf", fixture_bytes("handwriting.pdf")
    )
    doc = await settled(session_client, response.json()["id"])

    assert doc["status"] == "ready"
    assert env.vision.calls == [2]
    chunks = chunk_rows(db, doc["id"])
    second = [c for c in chunks if c["page_from"] == 2]
    assert second
    assert {c["extractor"] for c in second} == {"vlm"}
    assert {c["page_to"] for c in second} == {2}
    assert any("Call Sam on Friday" in c["text"] for c in second)
    assert {c["extractor"] for c in chunks if c["page_from"] == 1} == {"docling"}
    assert [c["ordinal"] for c in chunks] == list(range(len(chunks)))


async def _write_folder_file(env: ExtractEnv, name: str, data: bytes) -> str:
    """A file written into the project's folder from outside Tumnis, as a user's sync tool
    would; returns its path relative to the folder."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    async def one() -> Any:
        yield data

    async with (
        tenant_session(env.ws.ctx) as s,
        knowledge.open_backend(s, env.location_id, net=NetPolicy(mode="self-hosted")) as backend,
    ):
        await backend.write(f"{env.folder}/{name}", one(), None)
    return name


async def _wait_terminal(db: DbUrls, document_id: object, timeout_s: float = 60) -> str:
    deadline = time.monotonic() + timeout_s
    while True:
        status = str(scalar(db, "SELECT status FROM documents WHERE id = %s", document_id))
        if status in {"ready", "quarantined", "failed"}:
            return status
        if time.monotonic() > deadline:
            raise TimeoutError(f"{document_id} still {status}")
        await asyncio.sleep(0.1)


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P1-16")
@pytest.mark.xfail(strict=True, reason="spec:P1-16")
async def test_folder_file_extracted_through_same_pipeline(
    extract_env: ExtractEnv, dbos: type[DBOS], db: DbUrls
) -> None:
    """T-P1-16-11
    A file found in the project folder runs with `source = "storage"`: scanned first,
    extracted, never placed again (its path is unchanged), and tainted. An EICAR file found
    there is quarantined and stays where it is: Tumnis never deletes an outside file.
    """
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    env = extract_env
    body = b"# Outside\n\nWritten by a person.\n"
    await _write_folder_file(env, "notes/from-outside.md", body)
    found = await knowledge.ingest_folder_file(
        env.ws.ctx, project_id=env.project_id, path="notes/from-outside.md", size=len(body)
    )
    assert await _wait_terminal(db, found.id) == "ready"
    assert env.steps == [s for s in PIPELINE if s != "place"]
    row = rows(db, "SELECT path, tainted, trust, status FROM documents WHERE id = %s", found.id)[0]
    assert row == {
        "path": "notes/from-outside.md",
        "tainted": True,
        "trust": "untrusted",
        "status": "ready",
    }
    assert len(chunk_rows(db, str(found.id))) == 1

    env.steps.clear()
    await _write_folder_file(env, "dropped.txt", eicar())
    bad = await knowledge.ingest_folder_file(
        env.ws.ctx, project_id=env.project_id, path="dropped.txt", size=len(eicar())
    )
    assert await _wait_terminal(db, bad.id) == "quarantined"
    assert env.steps == ["read", "scan", "quarantine"]
    assert env.extractor.calls == [("from-outside.md", "markdown")]
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    async with (
        tenant_session(env.ws.ctx) as s,
        knowledge.open_backend(s, env.location_id, net=NetPolicy(mode="self-hosted")) as backend,
    ):
        assert await backend.stat(f"{env.folder}/dropped.txt") is not None
        assert await backend.stat(f"{env.folder}/uploads/dropped.txt") is None


async def _status(worker: Any, workflow_id: str) -> Any:
    handle = worker.dbos_client().retrieve_workflow(workflow_id)
    return await asyncio.to_thread(handle.get_status)


async def _until_success(worker: Any, ids: list[str], timeout_s: float = 60) -> dict[str, str]:
    """Wait until every workflow in `ids` succeeded; each one's executor id."""
    deadline = time.monotonic() + timeout_s
    while True:
        statuses = {wid: await _status(worker, wid) for wid in ids}
        if all(s.status == "SUCCESS" for s in statuses.values()):
            return {wid: s.executor_id for wid, s in statuses.items()}
        if time.monotonic() > deadline:
            raise TimeoutError({wid: s.status for wid, s in statuses.items()})
        await asyncio.sleep(0.2)


@pytest.mark.req("ADR-0007")
@pytest.mark.wp("P1-16")
@pytest.mark.xfail(strict=True, reason="spec:P1-16")
async def test_extract_queue_only_on_worker_extract(worker_killer: WorkerKillerFactory) -> None:
    """T-P1-16-12
    Two workers share one system database. The main worker dequeues every queue but
    `extract`: a probe workflow enqueued there waits, still ENQUEUED, while the main
    worker runs the others. The extract worker then runs that probe and dequeues nothing
    else: a later probe on another queue is run by the main worker.
    """
    main = worker_killer("never", events=0, imports=(QUEUE_PROBE,))
    extract = worker_killer("never", events=0, imports=(QUEUE_PROBE,), queues=("extract",))
    main_proc = await main.start_worker(armed=False)
    await main._migrated(main_proc, 60)

    async def enqueue(queue: str, workflow_id: str) -> None:
        options: dict[str, Any] = {
            "queue_name": queue,
            "workflow_name": "knowledge_queue_probe",
            "workflow_id": workflow_id,
            "app_version": KILLER_APP_VERSION,
        }
        await main.dbos_client().enqueue_async(options, queue)  # type: ignore[arg-type]

    await enqueue("extract", "probe-extract")
    await enqueue("maintenance", "probe-maintenance-1")
    await enqueue("sync", "probe-sync")
    others = await _until_success(main, ["probe-maintenance-1", "probe-sync"])
    await asyncio.sleep(2)
    assert (await _status(main, "probe-extract")).status == "ENQUEUED"

    await extract.start_worker(armed=False)
    assert (await _until_success(extract, ["probe-extract"])) == {"probe-extract": "worker-extract"}
    await enqueue("maintenance", "probe-maintenance-2")
    later = await _until_success(main, ["probe-maintenance-2"])
    assert "worker-extract" not in {*others.values(), *later.values()}
