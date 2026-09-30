"""A new project gets its Tumnis-made folder (P1-15, FR-15.12): named after the project on
the workspace default location, with `uploads/`, `notes/`, `agent-outputs/` and Tumnis's
own `.tumnis/`, made once however often the event is delivered."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import psycopg
import pytest

from tests._pg import OWNER
from tumnis.modules.knowledge.tests.integration._folder_runner import SELF_HOSTED

if TYPE_CHECKING:
    from pathlib import Path

    from dbos import DBOS, WorkflowHandleAsync

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

FOLDER_SUBSCRIBER = "knowledge.assign_project_folder"
MARKER = ".tumnis-root"
LAYOUT = [".tumnis", "agent-outputs", "notes", "uploads"]


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P1-15")
@pytest.mark.xfail(strict=True, reason="spec:P1-15")
async def test_new_project_gets_tumnis_made_folder(
    db: DbUrls,
    dbos: type[DBOS],
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    tmp_location: Path,
) -> None:
    """T-P1-15-13
    `project.created` makes the project's folder on the default location, named after the
    project (sanitized), with its three subfolders and `.tumnis/`, recorded as
    `tumnis_made`; delivering the event again, or asking for the folder again, makes
    nothing more (one folder row, one folder on disk, no file written).
    """
    from tumnis.core.events import delivery_id, relay_once  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge import events as knowledge_events  # noqa: PLC0415
    from tumnis.modules.knowledge import sync  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    ws = knowledge_ws
    sync.configure(net=SELF_HOSTED)
    try:
        async with tenant_session(ws.ctx) as s:
            await knowledge.create_location(
                s,
                knowledge.LocationIn(name="disk", kind="server_path", root=str(tmp_location)),
                net=SELF_HOSTED,
            )
        async with tenant_session(ws.ctx) as s:
            project = await projects.create_project(
                s, ws.ctx.actor, projects.ProjectCreate(name="Acme: Site?"), now=clock.now()
            )
        assert await relay_once() >= 1
        with psycopg.connect(db.libpq(OWNER)) as conn:
            row = conn.execute(
                "SELECT event_id FROM outbox WHERE name = 'project.created'"
            ).fetchone()
        assert row is not None
        delivery: WorkflowHandleAsync[str] = await dbos.retrieve_workflow_async(
            delivery_id(row[0], FOLDER_SUBSCRIBER)
        )
        assert await delivery.get_result() == "delivered"

        async with tenant_session(ws.ctx) as s:
            folder = await knowledge.get_project_folder(s, project.id)
        assert (folder.mode, folder.root_path) == ("tumnis_made", "Acme- Site-")
        made = tmp_location / folder.root_path
        assert sorted(os.listdir(made)) == LAYOUT

        # Once: the subscriber again (a redelivery), and the api call itself.
        with psycopg.connect(db.libpq(OWNER)) as conn:
            envelope = conn.execute(
                "SELECT event_id, name, schema_version, workspace_id, occurred_at, payload"
                " FROM outbox WHERE name = 'project.created'"
            ).fetchone()
        assert envelope is not None
        from tumnis.core.events import EventEnvelope  # noqa: PLC0415

        await knowledge_events.assign_project_folder(
            EventEnvelope(
                event_id=envelope[0],
                name=envelope[1],
                schema_version=envelope[2],
                workspace_id=envelope[3],
                occurred_at=envelope[4],
                actor="system",
                payload=envelope[5],
            )
        )
        async with tenant_session(ws.ctx) as s:
            again = await knowledge.ensure_project_folder(s, project.id, net=SELF_HOSTED)
        assert again is not None
        assert again.root_path == folder.root_path
    finally:
        sync.configure(net=None)

    with psycopg.connect(db.libpq(OWNER)) as conn:
        count = conn.execute("SELECT count(*) FROM project_folders").fetchone()
    assert count is not None
    assert count[0] == 1
    assert sorted(os.listdir(tmp_location)) == sorted([MARKER, folder.root_path])
    assert sorted(os.listdir(made)) == LAYOUT
    assert [p for p in made.rglob("*") if p.is_file()] == []
