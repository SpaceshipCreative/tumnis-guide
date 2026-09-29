"""The project brief is the pinned first knowledge entry (P0-17, FR-2.3, R-13): the
`project.created` subscriber writes it once, however often the event is delivered."""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    from dbos import DBOS, WorkflowHandleAsync

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

# The knowledge subscriber of `project.created`; its name is part of every delivery's
# workflow ID, so it is spelled out here.
BRIEF_SUBSCRIBER = "knowledge.create_brief"


@pytest.mark.req("FR-2.3")
@pytest.mark.wp("P0-17")
async def test_brief_created_once_per_project(
    db: DbUrls, dbos: type[DBOS], workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """T-P0-17-18
    A project created with `brief_md="# Goal"` emits `project.created`; the relay delivers
    it to the knowledge subscriber, then the same envelope is delivered again under a new
    workflow ID. Exactly one `documents` row exists with `role='brief'`, `kind='text'`,
    pinned, trusted, not tainted, `body_md='# Goal'`, and `knowledge.api.get_brief`
    returns it as a `DocumentDTO`.
    """
    from dbos import SetWorkflowID  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415

    import tumnis.modules.knowledge.events  # noqa: F401, PLC0415  # registers the subscriber
    from tumnis.core.events import (  # noqa: PLC0415
        EVENTS_QUEUE,
        EventEnvelope,
        deliver_event,
        delivery_id,
        relay_once,
        subscribers_for,
    )
    from tumnis.core.outbox import outbox_table  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    assert BRIEF_SUBSCRIBER in {sub.name for sub in subscribers_for("project.created")}
    actor = workspace.ctx.actor
    async with tenant_session(workspace.ctx) as s:
        project = await projects.create_project(
            s, actor, projects.ProjectCreate(name="Acme", brief_md="# Goal"), now=clock.now()
        )
    assert await relay_once() >= 1
    async with tenant_session(workspace.ctx) as s:
        row = (
            (await s.execute(select(outbox_table).where(outbox_table.c.name == "project.created")))
            .mappings()
            .one()
        )
    envelope = EventEnvelope.from_outbox_row(row)
    first: WorkflowHandleAsync[str] = await dbos.retrieve_workflow_async(
        delivery_id(envelope.event_id, BRIEF_SUBSCRIBER)
    )
    assert await first.get_result() == "delivered"
    with SetWorkflowID(f"{delivery_id(envelope.event_id, BRIEF_SUBSCRIBER)}:again"):
        again: WorkflowHandleAsync[str] = await dbos.enqueue_workflow_async(
            EVENTS_QUEUE, deliver_event, BRIEF_SUBSCRIBER, envelope.model_dump(mode="json")
        )
    assert await again.get_result() == "delivered"

    with psycopg.connect(db.libpq(OWNER)) as conn:
        rows = conn.execute(
            "SELECT kind, pinned, trust, tainted, body_md, project_id FROM documents"
            " WHERE role = 'brief'"
        ).fetchall()
    assert rows == [("text", True, "trusted", False, "# Goal", project.id)]

    async with tenant_session(workspace.ctx):
        brief = await knowledge.get_brief(project.id)
    assert isinstance(brief, knowledge.DocumentDTO)
    assert brief.project_id == project.id
    assert brief.role == "brief"
    assert brief.body_md == "# Goal"
    assert brief.pinned is True
    assert brief.trust == "trusted"
    assert brief.tainted is False
