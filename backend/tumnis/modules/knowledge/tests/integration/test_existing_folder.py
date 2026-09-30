"""A project in a folder the user already keeps (P3-14, FR-15.12, SEC-3): Tumnis indexes
what it finds, writes only inside a `Tumnis/` subfolder, never renames, moves or
overwrites a file it did not create, and deletes one only when the user confirms it in the
app (never at an agent's request, whatever its scopes)."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._pg import OWNER
from tumnis.modules.knowledge.tests.integration._folder_runner import FolderRunner, make_root

if TYPE_CHECKING:
    from pathlib import Path

    import httpx
    from dbos import DBOS

    from tests._pg import DbUrls
    from tests.fixtures import KeyClientFactory, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

CLIENT = "acme-client"  # the user's own folder on the share
OUTSIDE = {
    "Contracts/SOW.pdf": b"%PDF-1.4 statement of work",
    "notes.md": b"# Their notes\n",
}


def _rows(db: DbUrls, sql: str, *params: object) -> list[tuple[Any, ...]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        return conn.execute(sql, params).fetchall()


def _client_folder(root: Path) -> Path:
    """The user's folder, filled before Tumnis ever sees it, with day-old mtimes."""
    folder = root / CLIENT
    for rel, data in OUTSIDE.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        day_ago = time.time() - 86_400
        os.utime(path, (day_ago, day_ago))
    return folder


def _snapshot(folder: Path) -> dict[str, tuple[bytes, int]]:
    return {
        rel: ((folder / rel).read_bytes(), (folder / rel).stat().st_mtime_ns) for rel in OUTSIDE
    }


async def _existing(db: DbUrls, ws: WorkspaceHandle, clock: FixedClock, root: Path) -> FolderRunner:
    runner = FolderRunner(
        db=db, ws=ws, clock=clock, backend="share", mode="existing", existing_path=CLIENT
    )
    runner.root = root
    await runner.start()
    return runner


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P3-14")
async def test_outside_files_never_renamed_moved_or_overwritten(  # one script
    db: DbUrls,
    dbos: type[DBOS],
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    tmp_path: Path,
) -> None:
    """T-P3-14-06
    A share holding the user's folder (`Contracts/SOW.pdf`, `notes.md`) becomes a project's
    existing folder: setup makes `Tumnis/` with `uploads/`, `notes/`, `agent-outputs/` and
    `.trash/` and writes no file; the sync indexes both files as outside (tainted). Then a
    scripted run: the user writes and edits a note, an upload lands, an outside edit
    conflicts with a Tumnis-made note, Tumnis's text of an outside file differs from it, a
    note is deleted, the user renames an outside document in the app and deletes one (no
    confirmation). The outside files keep their bytes, names and mtimes; every write, move
    and delete Tumnis made is inside `Tumnis/`; the rename changed the title only.
    """
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    ws = knowledge_ws
    root = make_root(tmp_path / "share")
    folder = _client_folder(root)
    before = _snapshot(folder)
    runner = await _existing(db, ws, clock, root)
    try:
        assert runner.folder_root == CLIENT
        async with tenant_session(ws.ctx) as s:
            made = await knowledge.get_project_folder(s, runner.project_id)  # type: ignore[arg-type]
        assert made.mode == "existing"
        for sub in ("uploads", "notes", "agent-outputs", ".trash"):
            assert (folder / "Tumnis" / sub).is_dir(), sub
        assert runner.storage_log == []
        assert sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()) == [
            "Contracts/SOW.pdf",
            "notes.md",
        ]

        await runner.sync()
        await runner.expect(
            {
                "documents": {"count": 2},
                "document": {"path": "Contracts/SOW.pdf", "tainted": True},
            }
        )

        await runner.tumnis_create_note(title="Plan", body="v1\n")
        await runner.tumnis_create_note(title="Scratch", body="scratch\n")
        await runner.sync()
        await runner.tumnis_edit_note(title="Plan", body="v2\n")
        await runner.tumnis_save_note(title="Plan", status="written")
        await runner.upload(name="brief.txt", content="agent brief\n")
        await runner.outside_write("Tumnis/notes/Plan.md", append="outside line\n")
        await runner.tumnis_edit_note(title="Plan", body="v3\n")
        runner.force_document_body(path="notes.md", body="# Tumnis's version\n")
        await runner.tumnis_delete(title="Scratch")
        await runner.sync()
        await runner.sync()

        sow, _version = runner._doc_by_path("Contracts/SOW.pdf")
        async with tenant_session(ws.ctx) as s:
            renamed = await knowledge.rename_document(s, sow, title="Statement of work")
        assert renamed.title == "Statement of work"
        await runner.tumnis_delete(path="notes.md")
        await runner.sync()
        await runner.sync()

        assert _snapshot(folder) == before
        outside_now = sorted(p for p in await runner.listing() if not p.startswith("Tumnis/"))
        assert outside_now == sorted(OUTSIDE)
        assert await runner.trash() == ["notes/Scratch.md"]
        assert runner.storage_log, "Tumnis wrote its own files"
        for _kind, path in runner.storage_log:
            assert path.startswith(f"{CLIENT}/Tumnis/"), path
        for src, dst in runner.move_log:
            assert src.startswith(f"{CLIENT}/Tumnis/"), src
            assert dst.startswith(f"{CLIENT}/Tumnis/"), dst
        ((title, path),) = _rows(
            db,
            "SELECT d.title, f.path FROM documents d JOIN folder_files f ON f.document_id = d.id"
            " WHERE d.id = %s",
            sow,
        )
        assert (title, path) == ("Statement of work", f"{CLIENT}/Contracts/SOW.pdf")
    finally:
        await runner.close()


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P3-14")
async def test_agent_delete_of_external_file_refused(  # noqa: PLR0917  # fixtures
    db: DbUrls,
    dbos: type[DBOS],
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    tmp_path: Path,
    key_client: KeyClientFactory,
) -> None:
    """T-P3-14-07
    An agent (an API key with every knowledge scope) deleting an outside file's document
    gets 403 `external_delete_forbidden` and the refusal is audited; the delete-at-source
    route takes a user's session only (403 for the key), and `knowledge.api` refuses an
    agent actor on its own. The document stays, and the file stays through later syncs.
    """
    from tumnis.core.errors import ProblemError  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge.rules import ActorKind  # noqa: PLC0415

    ws = knowledge_ws
    root = make_root(tmp_path / "share")
    folder = _client_folder(root)
    before = _snapshot(folder)
    runner = await _existing(db, ws, clock, root)
    try:
        await runner.sync()
        sow, _version = runner._doc_by_path("Contracts/SOW.pdf")
        agent: httpx.AsyncClient = await key_client(["knowledge:write", "context:read"])
        refused = await agent.delete(f"/v1/knowledge/documents/{sow}")
        assert refused.status_code == 403, refused.text
        assert refused.json()["code"] == "external_delete_forbidden"
        at_source = await agent.post(
            f"/v1/knowledge/documents/{sow}/delete-at-source",
            json={"confirm_token": "x" * 32, "reason": "cleanup"},
        )
        assert at_source.status_code == 403, at_source.text
        with pytest.raises(ProblemError) as direct:
            async with tenant_session(ws.ctx) as s:
                await knowledge.delete_document(s, sow, actor=ActorKind.agent)
        assert (direct.value.status, direct.value.code) == (403, "external_delete_forbidden")

        ((actor_type, target_id),) = _rows(
            db,
            "SELECT actor_type, target_id FROM audit_log"
            " WHERE action = 'knowledge.external_delete_refused'",
        )
        assert (actor_type, target_id) == ("api_key", sow)
        await runner.sync()
        await runner.sync()
        assert _rows(db, "SELECT deleted_at FROM documents WHERE id = %s", sow) == [(None,)]
        assert _snapshot(folder) == before
        await runner.expect({"deletes": 0})
    finally:
        await runner.close()


@pytest.mark.req("FR-15.12", "SEC-3")
@pytest.mark.wp("P3-14")
async def test_user_delete_needs_confirmation(  # noqa: PLR0917  # fixtures
    db: DbUrls,
    dbos: type[DBOS],
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    tmp_path: Path,
    session_client: httpx.AsyncClient,
) -> None:
    """T-P3-14-08
    The user deleting an outside file's document without confirming only unindexes it: the
    file stays through later syncs. Deleting at the source needs the token the confirmation
    dialog is issued: a made-up one is refused (422 `confirmation_invalid`) and deletes
    nothing; with the issued token and a reason the next sync deletes the file (and only
    it), and the deletion is audited with the reason.
    """
    ws = knowledge_ws
    root = make_root(tmp_path / "share")
    folder = _client_folder(root)
    runner = await _existing(db, ws, clock, root)
    try:
        await runner.sync()
        sow, _version = runner._doc_by_path("Contracts/SOW.pdf")
        notes, _version = runner._doc_by_path("notes.md")

        plain = await session_client.delete(f"/v1/knowledge/documents/{sow}")
        assert plain.status_code == 200, plain.text
        assert plain.json()["outcome"] == "index_only"
        await runner.sync()
        await runner.sync()
        assert (folder / "Contracts" / "SOW.pdf").read_bytes() == OUTSIDE["Contracts/SOW.pdf"]

        made_up = await session_client.post(
            f"/v1/knowledge/documents/{notes}/delete-at-source",
            json={"confirm_token": "x" * 32, "reason": "duplicate"},
        )
        assert made_up.status_code == 422, made_up.text
        assert made_up.json()["code"] == "confirmation_invalid"
        await runner.sync()
        assert (folder / "notes.md").is_file()

        issued = await session_client.post(f"/v1/knowledge/documents/{notes}/delete-confirmation")
        assert issued.status_code == 200, issued.text
        token = issued.json()["confirm_token"]
        confirmed = await session_client.post(
            f"/v1/knowledge/documents/{notes}/delete-at-source",
            json={"confirm_token": token, "reason": "a duplicate of the signed copy"},
        )
        assert confirmed.status_code == 202, confirmed.text
        await runner.sync()
        assert not (folder / "notes.md").exists()
        assert (folder / "Contracts" / "SOW.pdf").is_file()
        await runner.expect({"deletes": 1})
        ((actor_type, reason, target_id),) = _rows(
            db,
            "SELECT actor_type, reason, target_id FROM audit_log"
            " WHERE action = 'knowledge.deleted_at_source'",
        )
        assert (actor_type, reason, target_id) == ("user", "a duplicate of the signed copy", notes)
    finally:
        await runner.close()
