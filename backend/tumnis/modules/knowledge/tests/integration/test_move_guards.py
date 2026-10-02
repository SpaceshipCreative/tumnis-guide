"""Guards around deleting at the source and the move job (P3-14 review follow-up, #154,
FR-15.12): the delete-confirmation routes serve outside files only; a move never targets
the project's own folder, never runs twice at once for a project, and ends `failed` (never
stuck in `copying`) when the target conflicts or the source changed while it copied."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._pg import OWNER
from tumnis.modules.knowledge.tests.integration._folder_runner import make_root
from tumnis.modules.knowledge.tests.integration._locations import SELF_HOSTED
from tumnis.modules.knowledge.tests.integration.test_locations import (
    _project,
    _server_path_location,
)
from tumnis.modules.knowledge.tests.integration.test_move import _project_with_files, _tree

if TYPE_CHECKING:
    from pathlib import Path

    from dbos import DBOS

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _rows(db: DbUrls, sql: str, *params: object) -> list[tuple[Any, ...]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        return conn.execute(sql, params).fetchall()


@pytest.mark.req("FR-15.12", "SEC-3")
@pytest.mark.wp("P3-14")
async def test_delete_confirmation_only_for_outside_files(
    db: DbUrls, knowledge_ws: WorkspaceHandle, clock: FixedClock
) -> None:
    """A document Tumnis made (no outside file) goes to the trash; the delete-at-source
    flow refuses it (409 `not_an_outside_file`) before any token is issued or used."""
    from tumnis.core.errors import ProblemError  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    ws = knowledge_ws
    project_id = await _project(ws, clock)
    async with tenant_session(ws.ctx) as s:
        note = await knowledge.put_text_document(
            s, project_id, title="Plan", body_md="# Plan\n", role=None
        )
    user = uuid.uuid4()
    with pytest.raises(ProblemError) as issued:
        async with tenant_session(ws.ctx) as s:
            await knowledge.issue_delete_confirmation(s, note, user_id=user)
    assert (issued.value.status, issued.value.code) == (409, "not_an_outside_file")
    with pytest.raises(ProblemError) as used:
        async with tenant_session(ws.ctx) as s:
            await knowledge.delete_at_source(
                s, note, confirm_token="x" * 32, reason="cleanup", user_id=user
            )
    assert (used.value.status, used.value.code) == (409, "not_an_outside_file")
    assert _rows(db, "SELECT count(*) FROM delete_confirmations WHERE document_id = %s", note) == [
        (0,)
    ]
    assert _rows(db, "SELECT deleted_at FROM documents WHERE id = %s", note) == [(None,)]


def _outside_record(
    db: DbUrls, ws: WorkspaceHandle, location: uuid.UUID, doc: uuid.UUID, path: str
) -> None:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        conn.execute(
            "INSERT INTO folder_files (workspace_id, location_id, path, size, mtime,"
            " content_hash, etag, origin, document_id)"
            " VALUES (%s, %s, %s, 1, now(), 'h1', 'e1', 'external', %s)",
            (ws.id, location, path, doc),
        )


@pytest.mark.req("FR-15.12", "SEC-3")
@pytest.mark.wp("P3-14")
async def test_delete_at_source_needs_one_outside_file(
    db: DbUrls, knowledge_ws: WorkspaceHandle, clock: FixedClock, tmp_path: Path
) -> None:
    """A confirmation deletes one file: a document with more than one live file record is
    refused (409 `several_files`) before a token is issued or used; with one outside file
    the token is issued, and using it marks only that record."""
    from tumnis.core.errors import ProblemError  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    ws = knowledge_ws
    location = await _server_path_location(ws, make_root(tmp_path / "a"), "a")
    project_id = await _project(ws, clock)
    async with tenant_session(ws.ctx) as s:
        doc = await knowledge.put_text_document(
            s, project_id, title="SOW", body_md="statement\n", role=None
        )
    _outside_record(db, ws, location, doc, "acme/SOW.txt")
    _outside_record(db, ws, location, doc, "acme/copy/SOW.txt")
    user = uuid.uuid4()
    with pytest.raises(ProblemError) as issued:
        async with tenant_session(ws.ctx) as s:
            await knowledge.issue_delete_confirmation(s, doc, user_id=user)
    assert (issued.value.status, issued.value.code) == (409, "several_files")
    with pytest.raises(ProblemError) as used:
        async with tenant_session(ws.ctx) as s:
            await knowledge.delete_at_source(
                s, doc, confirm_token="x" * 32, reason="cleanup", user_id=user
            )
    assert (used.value.status, used.value.code) == (409, "several_files")

    with psycopg.connect(db.libpq(OWNER)) as conn:
        conn.execute("DELETE FROM folder_files WHERE path = 'acme/copy/SOW.txt'")
    async with tenant_session(ws.ctx) as s:
        token = (await knowledge.issue_delete_confirmation(s, doc, user_id=user)).confirm_token
    async with tenant_session(ws.ctx) as s:
        outcome = await knowledge.delete_at_source(
            s, doc, confirm_token=token, reason="cleanup", user_id=user
        )
    assert outcome == "delete_at_source"
    assert _rows(
        db, "SELECT path, delete_confirmed FROM folder_files WHERE document_id = %s", doc
    ) == [("acme/SOW.txt", True)]


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P3-14")
async def test_move_refuses_own_folder_and_a_second_move(
    knowledge_ws: WorkspaceHandle, clock: FixedClock, tmp_path: Path
) -> None:
    """A target inside the project's own folder is `same_folder`. While a move is
    `copying`, another move of the project is refused (`move_in_progress`), by the route's
    check and by `begin`; `begin` run again for the same move returns that move."""
    from tumnis.core.errors import ProblemError  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge import move  # noqa: PLC0415

    ws = knowledge_ws
    source = await _server_path_location(ws, make_root(tmp_path / "a"), "a")
    target = await _server_path_location(ws, make_root(tmp_path / "b"), "b", default=False)
    project_id = await _project(ws, clock)
    async with tenant_session(ws.ctx) as s:
        folder = await knowledge.ensure_project_folder(s, project_id, net=SELF_HOSTED)
    assert folder is not None

    async def refused(location: uuid.UUID, path: str) -> tuple[int, str]:
        async with tenant_session(ws.ctx) as s:
            with pytest.raises(ProblemError) as caught:
                await move.precheck(s, project_id, location, path)
        return caught.value.status, caught.value.code

    assert await refused(source, f"{folder.root_path}/inner") == (409, "same_folder")

    first = uuid.uuid4()
    started = await move.begin(str(ws.id), str(project_id), str(target), "moved", move_id=first)
    assert started["move_id"] == str(first), started
    again = await move.begin(str(ws.id), str(project_id), str(target), "moved", move_id=first)
    assert again == started
    second = await move.begin(
        str(ws.id), str(project_id), str(target), "elsewhere", move_id=uuid.uuid4()
    )
    assert second == {"error": "move_in_progress"}
    assert await refused(target, "elsewhere") == (409, "move_in_progress")


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P3-14")
async def test_move_target_conflict_fails_the_move(  # fixtures
    db: DbUrls,
    dbos: type[DBOS],
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    tmp_path: Path,
) -> None:
    """A different file already at a copy's path on the target ends the move `failed`
    (`target_conflict`): nothing is switched and the target's file is left as it was."""
    from dbos import SetWorkflowID  # noqa: PLC0415

    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge import workflows  # noqa: PLC0415

    ws = knowledge_ws
    root = make_root(tmp_path / "share")
    source, project_id, folder = await _project_with_files(ws, clock, root)
    target_root = make_root(tmp_path / "target")
    target = await _server_path_location(ws, target_root, "target", default=False)
    squatter = target_root / "acme-moved" / "notes" / "Plan.md"
    squatter.parent.mkdir(parents=True)
    squatter.write_bytes(b"someone else's plan\n")

    with SetWorkflowID(f"move-{uuid.uuid4()}"):
        result = await workflows.move_project_folder(
            str(ws.id), str(project_id), str(target), "acme-moved"
        )
    assert (result["status"], result["reason"]) == ("failed", "target_conflict"), result

    async with tenant_session(ws.ctx) as s:
        now = await knowledge.get_project_folder(s, project_id)
    assert (now.location_id, now.root_path) == (source, folder)
    assert squatter.read_bytes() == b"someone else's plan\n"
    assert _rows(
        db, "SELECT status, reason FROM folder_moves WHERE project_id = %s", project_id
    ) == [("failed", "target_conflict")]


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P3-14")
async def test_move_source_changed_while_copying_fails_the_switch(  # fixtures
    db: DbUrls,
    dbos: type[DBOS],
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    tmp_path: Path,
) -> None:
    """A file lands in the source folder after it was listed: the switch finds the source
    changed and ends the move `failed` (`changed_during_move`) in its own transaction; the
    project's folder and its file records still point at the source."""
    from dbos import SetWorkflowID  # noqa: PLC0415

    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge import move, workflows  # noqa: PLC0415

    ws = knowledge_ws
    root = make_root(tmp_path / "share")
    source, project_id, folder = await _project_with_files(ws, clock, root)
    records = _rows(db, "SELECT path, etag FROM folder_files WHERE location_id = %s", source)
    target = await _server_path_location(
        ws, make_root(tmp_path / "target"), "target", default=False
    )

    def late_write(path: str, data: bytes) -> bytes:
        (root / folder / "uploads" / "late.txt").write_bytes(b"saved during the move\n")
        return data

    previous = move.use_fault(late_write)
    try:
        with SetWorkflowID(f"move-{uuid.uuid4()}"):
            result = await workflows.move_project_folder(
                str(ws.id), str(project_id), str(target), "acme-moved"
            )
    finally:
        move.use_fault(previous)
    assert (result["status"], result["reason"]) == ("failed", "changed_during_move"), result

    async with tenant_session(ws.ctx) as s:
        now = await knowledge.get_project_folder(s, project_id)
    assert (now.location_id, now.root_path) == (source, folder)
    assert (
        _rows(db, "SELECT path, etag FROM folder_files WHERE location_id = %s", source) == records
    )
    assert _rows(
        db, "SELECT status, reason FROM folder_moves WHERE project_id = %s", project_id
    ) == [("failed", "changed_during_move")]


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P3-14")
async def test_move_switch_run_again_changes_nothing(  # fixtures
    db: DbUrls,
    dbos: type[DBOS],
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    tmp_path: Path,
) -> None:
    """A switch step run again after its transaction committed (DBOS runs a step at least
    once) finds the move switched and changes nothing."""
    from dbos import SetWorkflowID  # noqa: PLC0415

    from tumnis.modules.knowledge import move, workflows  # noqa: PLC0415

    ws = knowledge_ws
    root = make_root(tmp_path / "share")
    source, project_id, folder = await _project_with_files(ws, clock, root)
    before = _tree(root / folder)
    target = await _server_path_location(
        ws, make_root(tmp_path / "target"), "target", default=False
    )
    with SetWorkflowID(f"move-{uuid.uuid4()}"):
        result = await workflows.move_project_folder(
            str(ws.id), str(project_id), str(target), "acme-moved"
        )
    assert result["status"] == "switched", result
    snapshot = (
        "SELECT location_id, path, etag FROM folder_files"
        " WHERE location_id IN (%s, %s) ORDER BY path"
    )
    files_after = _rows(db, snapshot, source, target)
    record = {
        "move_id": result["move_id"],
        "project_id": str(project_id),
        "from_location": str(source),
        "from_path": folder,
        "to_location": str(target),
        "to_path": "acme-moved",
    }
    assert await move.switch(str(ws.id), record, [], {}) is None
    assert _rows(db, snapshot, source, target) == files_after
    assert _rows(
        db, "SELECT status, verified_count FROM folder_moves WHERE project_id = %s", project_id
    ) == [("switched", len(before))]


@pytest.mark.req("FR-15.12", "REL-3")
@pytest.mark.wp("P3-14")
async def test_move_switch_out_of_retries_fails_the_move(  # noqa: PLR0917  # fixtures
    db: DbUrls,
    dbos: type[DBOS],
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A switch step that runs out of retries ends the move `failed` (`switch_failed`)
    instead of leaving it `copying`: the project's folder and its file records still point
    at the source, and a later move of the project is accepted and switches."""
    from dbos import SetWorkflowID  # noqa: PLC0415

    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge import move, workflows  # noqa: PLC0415

    ws = knowledge_ws
    root = make_root(tmp_path / "share")
    source, project_id, folder = await _project_with_files(ws, clock, root)
    records = _rows(db, "SELECT path, etag FROM folder_files WHERE location_id = %s", source)
    before = _tree(root / folder)
    target = await _server_path_location(
        ws, make_root(tmp_path / "target"), "target", default=False
    )
    attempts: list[str] = []

    async def broken_switch(*_args: Any) -> str | None:
        attempts.append("switch")
        raise RuntimeError("the database went away mid-switch")

    with monkeypatch.context() as patched, SetWorkflowID(f"move-{uuid.uuid4()}"):
        patched.setattr(move, "switch", broken_switch)
        result = await workflows.move_project_folder(
            str(ws.id), str(project_id), str(target), "acme-moved"
        )
    assert (result["status"], result["reason"]) == ("failed", "switch_failed"), result
    assert len(attempts) == workflows.STEP_RETRY["max_attempts"]
    assert _rows(
        db, "SELECT status, reason FROM folder_moves WHERE project_id = %s", project_id
    ) == [("failed", "switch_failed")]

    async with tenant_session(ws.ctx) as s:
        now = await knowledge.get_project_folder(s, project_id)
    assert (now.location_id, now.root_path) == (source, folder)
    assert (
        _rows(db, "SELECT path, etag FROM folder_files WHERE location_id = %s", source) == records
    )
    assert _tree(root / folder) == before

    with SetWorkflowID(f"move-{uuid.uuid4()}"):
        again = await workflows.move_project_folder(
            str(ws.id), str(project_id), str(target), "acme-moved-again"
        )
    assert again["status"] == "switched", again


@pytest.mark.req("FR-15.12", "REL-3")
@pytest.mark.wp("P3-14")
async def test_move_switch_committed_on_last_attempt_stays_switched(  # noqa: PLR0917  # fixtures
    db: DbUrls,
    dbos: type[DBOS],
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decision 91: the switch step's last attempt commits and then raises (its retries run
    out). The move stays `switched` (failing a move only moves it out of `copying`), the
    project's folder points at the target, and the workflow returns the switched result."""
    from dbos import SetWorkflowID  # noqa: PLC0415

    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge import move, workflows  # noqa: PLC0415

    ws = knowledge_ws
    root = make_root(tmp_path / "share")
    _source, project_id, _folder = await _project_with_files(ws, clock, root)
    target = await _server_path_location(
        ws, make_root(tmp_path / "target"), "target", default=False
    )
    real_switch = move.switch
    last = workflows.STEP_RETRY["max_attempts"]
    attempts: list[int] = []

    async def commit_then_raise(*args: Any) -> str | None:
        attempts.append(len(attempts) + 1)
        if len(attempts) == last:
            assert await real_switch(*args) is None
        raise RuntimeError("the commit's acknowledgement was lost")

    with monkeypatch.context() as patched, SetWorkflowID(f"move-{uuid.uuid4()}"):
        patched.setattr(move, "switch", commit_then_raise)
        result = await workflows.move_project_folder(
            str(ws.id), str(project_id), str(target), "acme-moved"
        )
    assert len(attempts) == last
    assert result["status"] == "switched", result
    assert _rows(
        db, "SELECT status, reason FROM folder_moves WHERE project_id = %s", project_id
    ) == [("switched", None)]
    async with tenant_session(ws.ctx) as s:
        now = await knowledge.get_project_folder(s, project_id)
    assert (now.location_id, now.root_path) == (target, "acme-moved")
