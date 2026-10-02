"""Moving a project's folder to another location (P3-14, FR-15.12, REL-3): the move job
copies every file, verifies each copy's hash, switches the project's folder in one
transaction and keeps the old copy (the user is asked whether to remove it). A hash
mismatch fails the move with nothing switched, and a killed worker resumes where it was."""

from __future__ import annotations

import hashlib
import uuid
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._pg import OWNER
from tumnis.modules.knowledge.tests._sftp import raw_sftp
from tumnis.modules.knowledge.tests.integration._folder_runner import make_root
from tumnis.modules.knowledge.tests.integration._locations import (
    SELF_HOSTED,
    pinned_sftp_location,
)
from tumnis.modules.knowledge.tests.integration.test_locations import (
    _chunks,
    _project,
    _server_path_location,
)

if TYPE_CHECKING:
    from pathlib import Path

    from dbos import DBOS

    from tests._pg import DbUrls
    from tests._services import SftpEndpoint
    from tests.fixtures import WorkerKillerFactory, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

KILLED_EXIT = 137
PROBE = "tumnis.modules.knowledge.tests.integration._sync_probe"  # logs every write


def _rows(db: DbUrls, sql: str, *params: object) -> list[tuple[Any, ...]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        return conn.execute(sql, params).fetchall()


def _tree(folder: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()
    }


async def _no_extraction(*_args: Any) -> None:
    return None


async def _project_with_files(
    ws: WorkspaceHandle, clock: FixedClock, root: Path
) -> tuple[Any, Any, str]:
    """(source location id, project id, folder): a server path holding a project folder
    with a note Tumnis wrote, an uploaded file and an outside file the sync recorded."""
    from dbos import SetWorkflowID  # noqa: PLC0415

    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge import sync, workflows  # noqa: PLC0415

    source = await _server_path_location(ws, root, "disk")
    project_id = await _project(ws, clock)
    async with tenant_session(ws.ctx) as s:
        folder = await knowledge.ensure_project_folder(s, project_id, net=SELF_HOSTED)
        assert folder is not None
        note_id = await knowledge.put_text_document(
            s, project_id, title="Plan", body_md="# Plan\n", role=None
        )
    async with tenant_session(ws.ctx) as s:
        await knowledge.save_note(s, note_id, net=SELF_HOSTED)
        await knowledge.place_upload(s, project_id, "brief.txt", _chunks(b"brief"), net=SELF_HOSTED)
    outside = root / folder.root_path / "Contracts"
    outside.mkdir(parents=True)
    (outside / "SOW.pdf").write_bytes(b"%PDF-1.4 statement of work")
    sync.configure(net=SELF_HOSTED)
    previous = sync.use(clock=clock, extraction=_no_extraction)
    try:
        with SetWorkflowID(f"move-setup-sync-{uuid.uuid4()}"):
            await workflows.folder_sync(str(ws.id), str(source))
    finally:
        sync.use(clock=previous[0], extraction=previous[1])
        sync.configure(net=None)
    return source, project_id, folder.root_path


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P3-14")
@pytest.mark.xfail(strict=True, reason="spec:P3-14")
async def test_move_copies_verifies_switches_keeps_old(  # noqa: PLR0917  # fixtures
    db: DbUrls,
    dbos: type[DBOS],
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    tmp_path: Path,
    sftp_server: SftpEndpoint,
) -> None:
    """T-P3-14-14
    A project folder on a server path moves to SFTP: every file lands on the target with
    the same bytes (hashes match), the project's folder then points at the target, the
    file records follow it (origin and document kept), the old files are all still there,
    the move is recorded as switched with every file verified and the old copy kept, a
    `folder_move_old_copy` review item asks about it, and a sync of the target afterwards
    trashes nothing and makes no new document.
    """
    from dbos import SetWorkflowID  # noqa: PLC0415

    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge import sync, workflows  # noqa: PLC0415

    ws = knowledge_ws
    root = make_root(tmp_path / "share")
    source, project_id, folder = await _project_with_files(ws, clock, root)
    before = _tree(root / folder)
    assert set(before) >= {"notes/Plan.md", "uploads/brief.txt", "Contracts/SOW.pdf"}
    records_before = {
        path.removeprefix(folder + "/"): (origin, document_id)
        for path, origin, document_id in _rows(
            db, "SELECT path, origin, document_id FROM folder_files WHERE location_id = %s", source
        )
    }
    docs_before = _rows(db, "SELECT count(*) FROM documents WHERE deleted_at IS NULL")[0][0]
    target, sftp_root = await pinned_sftp_location(ws, sftp_server)

    with SetWorkflowID(f"move-{uuid.uuid4()}"):
        result = await workflows.move_project_folder(
            str(ws.id), str(project_id), str(target.id), "acme-moved"
        )
    assert result["status"] == "switched", result

    async with raw_sftp(sftp_server) as sftp:
        for rel, data in before.items():
            async with sftp.open(f"{sftp_root}/acme-moved/{rel}", "rb") as handle:
                copied = await handle.read()
            assert hashlib.sha256(copied).hexdigest() == hashlib.sha256(data).hexdigest(), rel
    async with tenant_session(ws.ctx) as s:
        now = await knowledge.get_project_folder(s, project_id)
    assert (now.location_id, now.root_path) == (target.id, "acme-moved")
    records_after = {
        path.removeprefix("acme-moved/"): (origin, document_id)
        for path, origin, document_id in _rows(
            db,
            "SELECT path, origin, document_id FROM folder_files WHERE location_id = %s",
            target.id,
        )
    }
    assert records_after == records_before
    assert _rows(db, "SELECT count(*) FROM folder_files WHERE location_id = %s", source)[0][0] == 0
    assert _tree(root / folder) == before  # the old copy is kept, untouched
    ((status, verified, old_kept),) = _rows(
        db, "SELECT status, verified_count, old_kept FROM folder_moves WHERE project_id = %s",
        project_id,
    )  # fmt: skip
    assert (status, verified, old_kept) == ("switched", len(before), True)
    assert (
        _rows(
            db,
            "SELECT count(*) FROM review_items WHERE kind = 'folder_move_old_copy'"
            " AND decided_at IS NULL AND deleted_at IS NULL",
        )[0][0]
        == 1
    )

    sync.configure(net=SELF_HOSTED)
    previous = sync.use(clock=clock, extraction=_no_extraction)
    try:
        with SetWorkflowID(f"move-after-sync-{uuid.uuid4()}"):
            await workflows.folder_sync(str(ws.id), str(target.id))
    finally:
        sync.use(clock=previous[0], extraction=previous[1])
        sync.configure(net=None)
    assert _rows(db, "SELECT count(*) FROM documents WHERE deleted_at IS NULL")[0][0] == docs_before


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P3-14")
@pytest.mark.xfail(strict=True, reason="spec:P3-14")
async def test_move_hash_mismatch_aborts_without_switch(  # fixtures
    db: DbUrls,
    dbos: type[DBOS],
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    tmp_path: Path,
) -> None:
    """T-P3-14-15
    A fault corrupts one file as it is copied: verification finds the mismatch, the move
    ends `failed` (`hash_mismatch`), the project's folder and its file records still point
    at the source, and the source files are untouched.
    """
    from dbos import SetWorkflowID  # noqa: PLC0415

    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge import move, workflows  # noqa: PLC0415

    ws = knowledge_ws
    root = make_root(tmp_path / "share")
    source, project_id, folder = await _project_with_files(ws, clock, root)
    before = _tree(root / folder)
    records = _rows(db, "SELECT path, etag FROM folder_files WHERE location_id = %s", source)
    target_root = make_root(tmp_path / "target")
    target = await _server_path_location(ws, target_root, "target", default=False)

    def corrupt(path: str, data: bytes) -> bytes:
        return data + b"!" if path.endswith("notes/Plan.md") else data

    previous = move.use_fault(corrupt)
    try:
        with SetWorkflowID(f"move-{uuid.uuid4()}"):
            result = await workflows.move_project_folder(
                str(ws.id), str(project_id), str(target), "acme-moved"
            )
    finally:
        move.use_fault(previous)
    assert (result["status"], result["reason"]) == ("failed", "hash_mismatch"), result

    async with tenant_session(ws.ctx) as s:
        now = await knowledge.get_project_folder(s, project_id)
    assert (now.location_id, now.root_path) == (source, folder)
    assert (
        _rows(db, "SELECT path, etag FROM folder_files WHERE location_id = %s", source) == records
    )
    assert _rows(db, "SELECT count(*) FROM folder_files WHERE location_id = %s", target)[0][0] == 0
    assert _tree(root / folder) == before
    ((status,),) = _rows(db, "SELECT status FROM folder_moves WHERE project_id = %s", project_id)
    assert status == "failed"


@pytest.mark.req("REL-3")
@pytest.mark.wp("P3-14")
@pytest.mark.slow
@pytest.mark.xfail(strict=True, reason="spec:P3-14")
async def test_move_resumes_after_kill(  # noqa: PLR0917  # fixtures
    db: DbUrls,
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    tmp_path: Path,
    worker_killer: WorkerKillerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-P3-14-16
    A folder of 250 files moves in batches of 100: the worker is killed after batch 2; a
    fresh worker resumes the same move, copies the last 50, verifies and switches. Every
    file is written to the target exactly once (the write log outlives the killed worker).
    """
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    ws = knowledge_ws
    log = tmp_path / "writes.log"
    log.touch()
    monkeypatch.setenv("TUMNIS_TEST_WRITE_LOG", str(log))
    root = make_root(tmp_path / "share")
    await _server_path_location(ws, root, "disk")
    project_id = await _project(ws, clock)
    async with tenant_session(ws.ctx) as s:
        folder = await knowledge.ensure_project_folder(s, project_id, net=SELF_HOSTED)
    assert folder is not None
    files = root / folder.root_path / "files"
    files.mkdir()
    for n in range(250):
        (files / f"f{n:03}.txt").write_text(f"file {n}\n")
    target_root = make_root(tmp_path / "target")
    target = await _server_path_location(ws, target_root, "target", default=False)

    killer = worker_killer("knowledge.move_project_folder.batch_2", events=0, imports=[PROBE])
    workflow_id = f"move-kill-{uuid.uuid4()}"
    code = await killer.enqueue_until_killed(
        queue_name="sync",
        workflow_name="knowledge_move_project_folder",
        workflow_id=workflow_id,
        args=[str(ws.id), str(project_id), str(target), "acme-moved"],
    )
    assert code == KILLED_EXIT, killer.log_tail()
    assert len(log.read_text().splitlines()) == 200

    assert await killer.restart_until_done(workflow_id) == "SUCCESS", killer.log_tail()
    written = log.read_text().splitlines()
    assert len(written) == 250
    assert len(set(written)) == 250
    moved = target_root / "acme-moved" / "files"
    assert sorted(p.name for p in moved.iterdir()) == sorted(p.name for p in files.iterdir())
    async with tenant_session(ws.ctx) as s:
        now = await knowledge.get_project_folder(s, project_id)
    assert (now.location_id, now.root_path) == (target, "acme-moved")
