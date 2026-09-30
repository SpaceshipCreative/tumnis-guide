"""A seed project survives an archive and unarchive round trip (P2-18, FR-5.10).

The seed set's Acme project gets what an archive touches: its agent profile on a (fake,
protocol 2) runner with a home on the agent server, finished runs with their logs, and a
Tumnis-made folder with files and their index. It is archived and unarchived through the
routes, with the in-process `dbos` fixture running the workflows; afterwards every piece
is equal to what it was.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.projects.tests.integration._archive import ArchiveWorld, owner_rows

if TYPE_CHECKING:
    from pathlib import Path

    from dbos import DBOS
    from fastapi import FastAPI

    from tests._pg import DbUrls
    from tumnis.core.clock import FixedClock
    from tumnis.seed import SeedResult

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]

AGENT = "acme-site"
HOME: dict[str, bytes] = {
    "SOUL.md": b"# Acme site agent\n\nWrite plainly.\n",
    "memories/client.md": b"The client prefers short emails.\n" * 20,
    "sessions/2026/01.jsonl": b'{"role": "user", "text": "hi"}\n' * 50,
    "skills/tumnis/empty.txt": b"",
}
FILES: dict[str, bytes] = {
    "uploads/terms.txt": b"Net 30 days. Invoices in EUR.\n" * 30,
    "notes/kickoff.md": b"# Kickoff\n\nThe client wants a friendlier mark.\n",
    "uploads/empty.txt": b"",
}


def _tasks(db: DbUrls, project_id: uuid.UUID) -> list[tuple[Any, ...]]:
    return owner_rows(
        db,
        "SELECT id, parent_id, title, status, label, estimate_minutes, due_on, version,"
        " updated_at, deleted_at FROM tasks WHERE project_id = %s ORDER BY id",
        (project_id,),
    )


def _documents(db: DbUrls, project_id: uuid.UUID) -> list[tuple[Any, ...]]:
    return owner_rows(
        db,
        "SELECT id, title, kind, role, current_version_id, version, updated_at, deleted_at"
        " FROM documents WHERE project_id = %s ORDER BY id",
        (project_id,),
    )


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P2-18")
@pytest.mark.slow
@pytest.mark.xfail(strict=True, reason="spec:P2-18")
async def test_seed_project_survives_round_trip(  # noqa: PLR0915, PLR0917
    db: DbUrls,
    dbos: type[DBOS],
    seed: SeedResult,
    app: FastAPI,
    clock: FixedClock,
    tmp_path: Path,
) -> None:
    """T-P2-18-09
    The seed's Acme project, with its agent's home on the runner, three runs of logs and a
    Tumnis-made folder of three files: after an archive and an unarchive through the
    routes its tasks, documents, run logs, folder files and their index, and its agent's
    home (by manifest digest) are all equal to what they were, and nothing is left in
    `archived_blobs`.
    """
    from tests.acceptance._phase1 import seed_client  # noqa: PLC0415
    from tests.fakes.fake_runner import (  # noqa: PLC0415
        FakeRunner,
        create_runner,
        make_test_client,
        register_profile,
    )
    from tests.fixtures import WorkspaceHandle  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.core.types import ActorRef  # noqa: PLC0415

    workspace_id, user_id = seed.ids["ws_main"], seed.ids["u_scott"]
    handle = WorkspaceHandle(
        id=workspace_id,
        name="Scott's workspace",
        ctx=WorkspaceContext(workspace_id, ActorRef(f"user:{user_id}")),
        user_id=user_id,
    )
    project_id = seed.ids["p_acme"]
    http = await seed_client(app, clock)
    world = ArchiveWorld(db=db, ws=handle, clock=clock, client=http, root=tmp_path / "location")
    await world.start()
    runner_id, token = create_runner(handle, clock, "homelab-hermes")
    try:
        register_profile(handle, clock, AGENT, runner_id=runner_id, project_id=project_id)
        world.add_runs(project_id, runs=3, events=20, runner_id=runner_id)
        folder = await world.folder(project_id)
        world.write(folder, FILES)
        await world.sync()
        with make_test_client(app) as client:
            runner = FakeRunner(
                client, token, runner_id, name="homelab-hermes", profiles=[AGENT], clock=clock
            )
            runner.homes[AGENT] = dict(HOME)
            runner.connect_v2()
            try:
                tasks, documents = _tasks(db, project_id), _documents(db, project_id)
                events = world.run_events(project_id)
                tree, records = world.tree(folder), world.folder_files()
                home = FakeRunner.home_digest(runner.homes[AGENT])
                assert tasks
                assert documents
                assert len(events) == 60
                assert set(tree) >= set(FILES)

                await world.archive(project_id)
                assert AGENT not in runner.homes
                assert world.tree(folder) == {}
                await world.unarchive(project_id)

                assert _tasks(db, project_id) == tasks
                assert _documents(db, project_id) == documents
                assert world.run_events(project_id) == events
                assert world.tree(folder) == tree
                assert world.folder_files() == records
                assert world.packed_files(folder) == []
                assert FakeRunner.home_digest(runner.homes[AGENT]) == home
                assert world.blobs(project_id) == []
                assert runner.errors == []
            finally:
                runner.disconnect()
    finally:
        world.close()
