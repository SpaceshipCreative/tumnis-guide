"""The `folder_sync` workflow (P1-15, FR-15.12, FR-15.6): killed mid-apply it finishes
once, and an outside edit keeps the previous version readable."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.knowledge.tests.integration._folder_runner import (
    SELF_HOSTED,
    FolderRunner,
    make_root,
)

if TYPE_CHECKING:
    from pathlib import Path

    from dbos import DBOS

    from tests._pg import DbUrls
    from tests.fixtures import WorkerKillerFactory, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

KILLED_EXIT = 137
PROBE = "tumnis.modules.knowledge.tests.integration._sync_probe"
NOTES = ["Plan", "Budget", "Kickoff", "Risks", "Contacts"]  # with the brief: six new notes


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P1-15")
@pytest.mark.slow
@pytest.mark.xfail(strict=True, reason="spec:P1-15")
async def test_killed_mid_apply_finishes_once(  # noqa: PLR0917  # fixtures
    db: DbUrls,
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    tmp_path: Path,
    worker_killer: WorkerKillerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-P1-15-09
    A project folder with six notes Tumnis has not written yet (six decisions): the worker
    is killed after the third decision is applied; a fresh worker resumes the same
    `folder_sync`, applies the other three, and no file is written twice (the backend's
    write log, which outlives the killed worker, holds each path once).
    """
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    ws = knowledge_ws
    root = make_root(tmp_path / "share")
    log = tmp_path / "writes.log"
    log.touch()
    monkeypatch.setenv("TUMNIS_TEST_WRITE_LOG", str(log))
    async with tenant_session(ws.ctx) as s:
        location = await knowledge.create_location(
            s,
            knowledge.LocationIn(name="disk", kind="server_path", root=str(root), is_default=True),
            net=SELF_HOSTED,
        )
    async with tenant_session(ws.ctx) as s:
        project = await projects.create_project(
            s, ws.ctx.actor, projects.ProjectCreate(name="Acme"), now=clock.now()
        )
        await knowledge.create_brief(s, project.id, title="Acme brief", body_md="# Acme\n")
        await knowledge.assign_project_folder(s, project.id)
        for title in NOTES:
            await knowledge.put_text_document(
                s, project.id, title=title, body_md=f"# {title}\n", role=None
            )

    killer = worker_killer("knowledge.folder_sync.applied_3", imports=[PROBE])
    workflow_id = f"folder-sync-kill-{uuid.uuid4()}"
    code = await killer.enqueue_until_killed(
        queue_name="sync",
        workflow_name="knowledge_folder_sync",
        workflow_id=workflow_id,
        args=[str(ws.id), str(location.id)],
    )
    assert code == KILLED_EXIT, killer.log_tail()
    assert len(log.read_text().splitlines()) == 3

    assert await killer.restart_until_done(workflow_id) == "SUCCESS", killer.log_tail()
    written = log.read_text().splitlines()
    assert len(written) == 6
    assert len(set(written)) == 6
    notes = sorted(p.name for p in root.rglob("*.md"))
    assert notes == sorted([*(f"{t}.md" for t in NOTES), "Acme brief.md"])


@pytest.mark.req("FR-15.6")
@pytest.mark.wp("P1-15")
@pytest.mark.xfail(strict=True, reason="spec:P1-15")
async def test_versions_kept_on_outside_edit(
    db: DbUrls,
    dbos: type[DBOS],
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    tmp_path: Path,
) -> None:
    """T-P1-15-10
    A file edited outside becomes a new version of its Document on the next sync; the
    previous version is still readable, with its own text and hash.
    """
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    runner = FolderRunner(db=db, ws=knowledge_ws, clock=clock, backend="server_path")
    runner.root = make_root(tmp_path / "share")
    try:
        await runner.start()
        await runner.outside_write("uploads/terms.txt", "Net 30 days.\n")
        await runner.sync()
        await runner.outside_write("uploads/terms.txt", "Net 45 days.\n")
        await runner.sync()
        doc_id, _version = runner._doc_by_path("uploads/terms.txt")
        async with tenant_session(knowledge_ws.ctx) as s:
            versions = await knowledge.list_document_versions(s, doc_id)
            first = await knowledge.get_document_version(s, versions[0].id)
    finally:
        await runner.close()

    assert [v.version_no for v in versions] == [1, 2]
    assert first.body_md == "Net 30 days.\n"
    assert versions[1].body_md == "Net 45 days.\n"
    assert versions[0].content_hash != versions[1].content_hash
