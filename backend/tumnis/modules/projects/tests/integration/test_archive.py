"""Project archive and unarchive (P2-18, FR-5.10, FR-15.6, FR-15.12).

Archiving a project (`POST /v1/projects/{id}/archive`) runs `archive_project` behind the
route: the profile's home is archived on the agent server, its run logs and the project's
context items are compressed into `archived_blobs`, and its folder is packed into one
file when Tumnis made it (an existing folder stays; only its index is archived).
Unarchiving runs the reverse and restores everything byte for byte. A purge
(`POST /v1/purges`) is session-only and audited with its reason.
"""

from __future__ import annotations

import io
import tarfile
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from tests._pg import APP
from tumnis.modules.projects.tests.integration._archive import (
    ArchiveWorld,
    owner_exec,
    owner_rows,
)

if TYPE_CHECKING:
    from pathlib import Path

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import AppFactory, KeyClientFactory, WorkerKillerFactory, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]

FILES: dict[str, bytes] = {
    "uploads/logo.svg": b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>\n' * 40,
    "uploads/terms.txt": b"Net 30 days. Invoices in EUR.\n" * 30,
    "notes/kickoff.md": b"# Kickoff\n\nThe client wants a friendlier mark.\n",
    "agent-outputs/summary.md": b"# Summary\n\nThree drafts delivered.\n" * 10,
    "uploads/empty.txt": b"",
}
KILL_STEPS = [
    "projects.archive.begin",
    "projects.archive.profile_sent",
    "projects.archive.profile_stored",
    "projects.archive.run_logs",
    "projects.archive.excerpts",
    "projects.archive.folder",
    "projects.archive.finish",
]
PROBE = "tumnis.modules.projects.tests.integration._archive_probe"


def _members(packed: Path) -> dict[str, bytes]:
    """Regular files of a tar + zstd pack, by name."""
    import zstandard  # noqa: PLC0415

    raw = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(packed.read_bytes())).read()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
        return {
            m.name: tar.extractfile(m).read()  # type: ignore[union-attr]
            for m in tar.getmembers()
            if m.isfile()
        }


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P2-18")
@pytest.mark.xfail(strict=True, reason="spec:P2-18")
async def test_archive_compresses_logs_and_excerpts(archive_world: ArchiveWorld) -> None:
    """T-P2-18-03
    A project with three runs of forty log lines and two context items: archiving moves
    every `run_events` row of its runs and its context items into `archived_blobs` (the
    run logs taking fewer bytes than the rows' JSON did) and leaves none in their tables;
    unarchiving puts back rows equal to the originals, ids and times included, and leaves
    no blob behind.
    """
    world = archive_world
    project_id = await world.project("Acme site")
    world.add_runs(project_id, runs=3, events=40)
    world.add_context_items(project_id, 2)
    events = world.run_events(project_id)
    items = world.context_items(project_id)
    raw = world.run_events_bytes(project_id)
    assert len(events) == 120
    assert len(items) == 2

    await world.archive(project_id)

    assert world.run_events(project_id) == []
    assert world.context_items(project_id) == []
    logs = world.blobs(project_id, module="agents", kind="run_events")
    assert logs
    assert sum(row[4] for row in logs) < raw
    assert world.blobs(project_id, module="integrations", kind="context_items")

    await world.unarchive(project_id)

    assert world.run_events(project_id) == events
    assert world.context_items(project_id) == items
    assert world.blobs(project_id) == []


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P2-18")
@pytest.mark.xfail(strict=True, reason="spec:P2-18")
async def test_tumnis_made_folder_packed_into_one_file(archive_world: ArchiveWorld) -> None:
    """T-P2-18-04
    A Tumnis-made folder holding five files (one empty) is packed into one tar + zstd
    file on its location, outside the folder, holding exactly those files; once the pack
    is verified, no file is left under the folder.
    """
    world = archive_world
    project_id = await world.project("Acme site")
    folder = await world.folder(project_id)
    world.write(folder, FILES)
    await world.sync()
    before = world.tree(folder)
    assert set(before) >= set(FILES)

    await world.archive(project_id)

    assert world.tree(folder) == {}
    [packed] = world.packed_files(folder)
    assert packed.name.endswith(".tar.zst")
    members = _members(packed)
    assert set(members) == set(before)
    for rel, data in FILES.items():
        assert members[rel] == data


@pytest.mark.req("FR-15.6")
@pytest.mark.wp("P2-18")
@pytest.mark.xfail(strict=True, reason="spec:P2-18")
async def test_unarchive_restores_folder_byte_for_byte(archive_world: ArchiveWorld) -> None:
    """T-P2-18-05
    Archive then unarchive a Tumnis-made folder: every file is back with the same bytes
    (the tree's manifest is equal), the pack is gone, and each `folder_files` record's
    content hash is the sha256 of the file now on disk, as before.
    """
    world = archive_world
    project_id = await world.project("Acme site")
    folder = await world.folder(project_id)
    world.write(folder, FILES)
    await world.sync()
    before = world.tree(folder)
    records = world.folder_files()
    assert {row[1] for row in records} >= {f"{folder.name}/{rel}" for rel in FILES}

    await world.archive(project_id)
    await world.unarchive(project_id)

    assert world.tree(folder) == before
    assert world.packed_files(folder) == []
    assert world.folder_files() == records
    for _id, path, _size, content_hash, _origin, _doc in records:
        assert before[path.removeprefix(f"{folder.name}/")] == content_hash


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P2-18")
@pytest.mark.xfail(strict=True, reason="spec:P2-18")
async def test_existing_folder_stays_and_only_index_archived(
    archive_world: ArchiveWorld, db: DbUrls
) -> None:
    """T-P2-18-06
    A project pointing at an existing folder: archiving leaves every file in place with
    the same hash and packs nothing, while the folder's `folder_files` records and its
    documents' chunks move into `archived_blobs`; unarchiving brings back rows equal to
    the originals, the files still untouched.
    """
    world = archive_world
    project_id = await world.project("Old site")
    folder = await world.folder(project_id)
    world.write(folder, FILES)
    await world.sync()
    owner_exec(
        db, "UPDATE project_folders SET mode = 'existing' WHERE project_id = %s", (project_id,)
    )
    world.add_chunks(project_id, 2)
    before = world.tree(folder)
    records = world.folder_files()
    chunks = world.chunks(project_id)
    assert records
    assert len(chunks) == 2

    await world.archive(project_id)

    assert world.tree(folder) == before
    assert world.packed_files(folder) == []
    assert world.folder_files() == []
    assert world.chunks(project_id) == []
    assert world.blobs(project_id, module="knowledge")

    await world.unarchive(project_id)

    assert world.tree(folder) == before
    assert world.folder_files() == records
    assert world.chunks(project_id) == chunks
    assert world.blobs(project_id) == []


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P2-18")
@pytest.mark.xfail(strict=True, reason="spec:P2-18")
async def test_purge_is_session_only_and_audited(
    archive_world: ArchiveWorld,
    session_client: SessionClient,
    key_client: KeyClientFactory,
    db: DbUrls,
) -> None:
    """T-P2-18-07
    `POST /v1/purges {scope: "project", id, reason}` (R-37): an API key, whatever its
    scopes, is refused with 403; a project that is not archived is refused with 409
    `not_archived`; a missing or blank reason is a 422; the session's purge of the archived
    project is accepted (202), writes exactly one `data.purged` audit row carrying the
    reason and the project as its target, and the project is gone (404).
    """
    world = archive_world
    project_id = await world.project("Acme site")
    world.add_runs(project_id, runs=1, events=5)
    body: dict[str, Any] = {"scope": "project", "id": str(project_id), "reason": "Client asked"}

    live = await session_client.post("/v1/purges", json=body)
    assert live.status_code == 409, live.text
    assert live.json()["code"] == "not_archived"

    await world.archive(project_id)

    key = await key_client(["tasks:read", "tasks:write", "context:read", "knowledge:write"])
    refused = await key.post("/v1/purges", json=body)
    assert refused.status_code == 403, refused.text
    for reason in (None, "", "   "):
        bad = {**body, "reason": reason} if reason is not None else {**body}
        if reason is None:
            del bad["reason"]
        blank = await session_client.post("/v1/purges", json=bad)
        assert blank.status_code == 422, blank.text
    assert owner_rows(db, "SELECT count(*) FROM audit_log WHERE action = 'data.purged'") == [(0,)]

    purged = await session_client.post("/v1/purges", json=body)
    assert purged.status_code == 202, purged.text

    rows = owner_rows(
        db,
        "SELECT reason, target_type, target_id, actor_type FROM audit_log"
        " WHERE action = 'data.purged'",
    )
    assert rows == [("Client asked", "project", project_id, "user")]
    await world.relay()
    gone = await session_client.get(f"/v1/projects/{project_id}")
    assert gone.status_code == 404, gone.text


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P2-18")
@pytest.mark.slow
@pytest.mark.xfail(strict=True, reason="spec:P2-18")
@pytest.mark.parametrize("killpoint", KILL_STEPS)
async def test_killed_worker_resumes_archive_once(  # noqa: PLR0915, PLR0917
    killpoint: str,
    worker_killer: WorkerKillerFactory,
    app_factory: AppFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-P2-18-08
    A project with a profile on a (fake, protocol 2) runner, runs and a Tumnis-made
    folder, archived through the api. `archive_project` runs on a worker killed at
    `killpoint`; a fresh worker recovers it and it ends once: the project `archived`, one
    `archive` message sent and seen by the runner, the pack written exactly once (the
    backend's log outlives the killed worker) and the folder's files removed, the run logs
    in one blob and none left in `run_events`.
    """
    from tests.fakes.fake_runner import FakeRunner, create_runner, make_test_client  # noqa: PLC0415
    from tests.fixtures import KILLED_EXIT  # noqa: PLC0415
    from tumnis.core.clock import SystemClock  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    log = tmp_path / "storage.log"
    log.touch()
    monkeypatch.setenv("TUMNIS_TEST_ARCHIVE_LOG", str(log))
    killer = worker_killer(killpoint, events=0, imports=[PROBE])
    app = app_factory(dbos_system_database_url=killer.sys_db.url(APP))
    app.state.clock = SystemClock()
    world = ArchiveWorld(
        db=db,
        ws=workspace,
        clock=clock,
        client=None,  # type: ignore[arg-type]  # this test drives no route
        root=tmp_path / "location",
    )
    await world.start()
    runner_id, token = create_runner(workspace, clock, "homelab-hermes")
    try:
        async with tenant_session(workspace.ctx) as s:
            made = await projects.create_project(
                s, workspace.ctx.actor, projects.ProjectCreate(name="Acme site"), now=clock.now()
            )
        project_id = made.id
        folder = await world.folder(project_id)
        world.write(folder, FILES)
        world.add_runs(project_id, runs=3, events=10, profile="acme-site", runner_id=runner_id)
        async with tenant_session(workspace.ctx) as s:
            current = await projects.get_project(s, project_id)
            await projects.archive_project(
                s, workspace.ctx.actor, project_id, current.version, now=clock.now()
            )
        with make_test_client(app) as http:
            runner = FakeRunner(
                http, token, runner_id, name="homelab-hermes", profiles=["acme-site"], clock=clock
            )
            runner.homes["acme-site"] = {"SOUL.md": b"# Acme\n", "memories/a.md": b"notes\n"}
            runner.connect_v2()
            try:
                workflow_id = f"archive-kill-{uuid.uuid4()}"
                code = await killer.enqueue_until_killed(
                    queue_name="archive",
                    workflow_name="archive_project",
                    workflow_id=workflow_id,
                    args=[str(workspace.id), str(project_id), str(workspace.ctx.actor)],
                )
                assert code == KILLED_EXIT, killer.log_tail()
                status = await killer.restart_until_done(workflow_id)
                assert status == "SUCCESS", killer.log_tail()
            finally:
                runner.disconnect()
    finally:
        world.close()

    assert world.archive_state(project_id) == "archived"
    writes = [line for line in log.read_text().splitlines() if line.startswith("write ")]
    assert len(writes) == 1, writes
    assert writes[0].endswith(".tar.zst")
    assert world.tree(folder) == {}
    assert len(world.packed_files(folder)) == 1
    assert len(runner.archives()) == 1
    assert "acme-site" not in runner.homes
    [(sent,)] = owner_rows(
        db, "SELECT count(*) FROM runner_messages WHERE direction = 'out' AND type = 'archive'"
    )
    assert sent == 1
    assert world.run_events(project_id) == []
    assert len(world.blobs(project_id, module="agents", kind="run_events")) == 1
