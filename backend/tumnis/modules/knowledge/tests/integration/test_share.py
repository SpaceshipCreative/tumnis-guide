"""Shares (P3-14, FR-15.12): a mounted SMB or NFS folder is a server path of kind `share`.
Tumnis only checks its `.tumnis-root` marker (the user places it), so a dropped mount takes
the location offline instead of filling the server's own disk, and changes are found by
the 15-minute scan (size and mtime first, then a hash), never by file events."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.knowledge.tests.integration._folder_runner import FolderRunner, make_root
from tumnis.modules.knowledge.tests.integration._locations import (
    MARKER,
    SELF_HOSTED,
    share_location,
)
from tumnis.modules.knowledge.tests.integration.test_locations import (
    _chunks,
    _count,
    _files,
    _project,
    _server_path_location,
)

if TYPE_CHECKING:
    from pathlib import Path

    from dbos import DBOS

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P3-14")
@pytest.mark.xfail(strict=True, reason="spec:P3-14")
async def test_missing_marker_marks_offline_and_queues_writes(  # fixtures
    db: DbUrls,
    dbos: type[DBOS],
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    tmp_path: Path,
) -> None:
    """T-P3-14-12
    Setting up a share without its marker is refused (422 `marker_missing`, nothing saved).
    With the marker it is online. Removing the marker (a dropped mount): the folder sync
    finds it offline (`marker_missing`), a note save queues (the text is safe in Postgres),
    a file write is refused with 409 `location_offline`, and the mount point gets no file.
    """
    from dbos import SetWorkflowID  # noqa: PLC0415

    from tumnis.core.errors import ProblemError  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge import sync, workflows  # noqa: PLC0415

    ws = knowledge_ws
    bare = tmp_path / "bare"
    bare.mkdir()
    with pytest.raises(ProblemError) as refused:
        await share_location(ws, bare, name="bare")
    assert (refused.value.status, refused.value.code) == (422, "marker_missing")
    assert _count(db, "SELECT count(*) FROM storage_locations") == 0
    assert list(bare.iterdir()) == []

    root = make_root(tmp_path / "share")
    location = await share_location(ws, root)
    assert (location.kind, location.status) == ("share", "online")
    project_id = await _project(ws, clock)
    async with tenant_session(ws.ctx) as s:
        await knowledge.ensure_project_folder(s, project_id, net=SELF_HOSTED)
        note_id = await knowledge.put_text_document(
            s, project_id, title="Plan", body_md="# Plan\n", role=None
        )
    before = _files(root)

    (root / MARKER).unlink()
    sync.configure(net=SELF_HOSTED)
    try:
        with SetWorkflowID(f"share-sync-{location.id}"):
            result = await workflows.folder_sync(str(ws.id), str(location.id))
    finally:
        sync.configure(net=None)
    assert result["status"] == "offline"
    async with tenant_session(ws.ctx) as s:
        (offline,) = [loc for loc in await knowledge.list_locations(s) if loc.id == location.id]
    assert (offline.status, offline.status_reason) == ("offline", "marker_missing")

    async with tenant_session(ws.ctx) as s:
        queued = await knowledge.save_note(s, note_id, net=SELF_HOSTED)
    assert queued.status == "queued"
    assert _count(db, "SELECT count(*) FROM pending_writes") == 1
    with pytest.raises(ProblemError) as write:
        async with tenant_session(ws.ctx) as s:
            await knowledge.write_project_file(
                s, project_id, "uploads/a.pdf", _chunks(b"%PDF"), net=SELF_HOSTED
            )
    assert (write.value.status, write.value.code) == (409, "location_offline")
    assert _files(root) == before


def _age(path: Path, seconds: int) -> None:
    """Give the file an mtime `seconds` in the past, well outside any racy window."""
    then = time.time() - seconds
    os.utime(path, (then, then))


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P3-14")
@pytest.mark.xfail(strict=True, reason="spec:P3-14")
async def test_share_uses_scans_not_events(  # noqa: PLR0917  # fixtures
    db: DbUrls,
    dbos: type[DBOS],
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-P3-14-13
    A share is never watched (a local server path is, for contrast). The folder sync finds
    an outside file and an outside edit; a scan with nothing changed hashes no file (size
    and mtime match what was recorded); a touch without a content change is hashed once
    and makes no new version.
    """
    from tumnis.modules.knowledge import sync  # noqa: PLC0415
    from tumnis.modules.knowledge.adapters.server_path import ServerPathStorage  # noqa: PLC0415

    ws = knowledge_ws
    local = make_root(tmp_path / "local")
    local_id = await _server_path_location(ws, local, "local disk", default=False)
    hashed: list[Any] = []
    real_hash = ServerPathStorage._hash_fd

    def counting(fd: int) -> str:
        hashed.append(fd)
        return real_hash(fd)

    monkeypatch.setattr(ServerPathStorage, "_hash_fd", staticmethod(counting))

    runner = FolderRunner(db=db, ws=ws, clock=clock, backend="share")
    runner.root = make_root(tmp_path / "share")
    try:
        await runner.start()
        watched = {location for _ws, location, _root in await sync.watched_roots()}
        assert str(local_id) in watched
        assert str(runner.location_id) not in watched

        await runner.outside_write("Contracts/SOW.txt", "version one\n")
        _age(runner._disk("Contracts/SOW.txt"), 3600)
        await runner.sync()
        await runner.expect({"documents": {"count": 1}})
        await runner.sync()
        hashed.clear()
        await runner.sync()
        assert hashed == []  # nothing changed: size and mtime decide, no file is read

        await runner.outside_write("Contracts/SOW.txt", "version two\n")  # same size
        _age(runner._disk("Contracts/SOW.txt"), 1800)
        await runner.sync()
        assert hashed != []
        await runner.expect(
            {"document": {"path": "Contracts/SOW.txt", "versions": 2}, "documents": {"count": 1}}
        )

        hashed.clear()
        _age(runner._disk("Contracts/SOW.txt"), 900)  # touched, same bytes
        await runner.sync()
        assert hashed != []
        await runner.expect(
            {
                "document": {"path": "Contracts/SOW.txt", "versions": 2},
                "record": {"path": "Contracts/SOW.txt", "mtime_matches_file": True},
            }
        )
        hashed.clear()
        await runner.sync()
        assert hashed == []
    finally:
        await runner.close()
