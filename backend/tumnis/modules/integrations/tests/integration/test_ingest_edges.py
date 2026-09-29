"""Edges of ingest and linking beyond the spec table (P0-12)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.integrations.tests.integration._integrations import (
    T0,
    item,
    new_connection,
    page,
    rows,
)

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-14.3")
@pytest.mark.wp("P0-12")
async def test_a_person_seen_through_two_connections_is_one_row(
    app_db: DbUrls, workspace: WorkspaceHandle, connection: uuid.UUID
) -> None:
    """People upsert on workspace + primary email: the second connection updates the
    first row (case-insensitively) instead of adding one."""
    from tumnis.modules.integrations.adapters.fake import ScriptedConnector  # noqa: PLC0415
    from tumnis.modules.integrations.api import ingest_page  # noqa: PLC0415

    other = new_connection(app_db, workspace.id, provider="scripted_chat", kind="chat")
    casey = item("client@example.com", "person", email="Client@Example.com", name="Casey")
    renamed = item("CLIENT@example.com", "person", email="client@EXAMPLE.com", name="Casey C.")
    await ingest_page(workspace.ctx, connection, ScriptedConnector(), page(casey))
    second = await ingest_page(workspace.ctx, other, ScriptedConnector(), page(renamed))
    assert (second.inserted, second.updated) == (0, 1)
    [person] = rows(app_db, "people", connection)
    assert person["display_name"] == "Casey C."
    assert rows(app_db, "people", other) == []


@pytest.mark.req("FR-14.1")
@pytest.mark.wp("P0-12")
async def test_ingest_refuses_a_connection_outside_the_workspace(
    app_db: DbUrls, workspace: WorkspaceHandle
) -> None:
    """A connection id the workspace cannot see is NotFound, and nothing is stored."""
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core.versioning import NotFound  # noqa: PLC0415
    from tumnis.modules.integrations.adapters.fake import ScriptedConnector  # noqa: PLC0415
    from tumnis.modules.integrations.api import ingest_page  # noqa: PLC0415

    elsewhere = new_connection(app_db, make_workspace(app_db, "Other"))
    with pytest.raises(NotFound):
        await ingest_page(
            workspace.ctx, elsewhere, ScriptedConnector(), page(item("m1", subject="x"))
        )
    assert rows(app_db, "raw_payloads", elsewhere) == []


@pytest.mark.req("FR-14.2")
@pytest.mark.wp("P0-12")
async def test_links_to_events_and_documents_read_taint_from_their_modules(
    app_db: DbUrls, workspace: WorkspaceHandle, connection: uuid.UUID
) -> None:
    """An event (trusted by default) links untainted, a synced document tainted; a missing
    target is NotFound; a url target without a URL is refused."""
    from tumnis.core.versioning import NotFound  # noqa: PLC0415
    from tumnis.modules.calendar.api import EventRecord, upsert_events  # noqa: PLC0415
    from tumnis.modules.integrations.api import link_context  # noqa: PLC0415
    from tumnis.modules.knowledge.api import (  # noqa: PLC0415
        DocumentRecord,
        upsert_synced_documents,
    )

    event = EventRecord(
        external_id="e1", fetched_at=T0, start_at=T0, end_at=T0 + timedelta(minutes=30)
    )
    await upsert_events(workspace.ctx, connection, [event])
    doc = DocumentRecord(external_id="d1", fetched_at=T0, title="Spec")
    await upsert_synced_documents(workspace.ctx, connection, [doc])
    [event_row] = rows(app_db, "events", connection)
    [doc_row] = rows(app_db, "documents", connection)
    task = uuid.uuid4()

    linked_event = await link_context(
        workspace.ctx,
        owner_type="task",
        owner_id=task,
        target_type="event",
        target_id=event_row["id"],
        added_by="user",
    )
    linked_doc = await link_context(
        workspace.ctx,
        owner_type="task",
        owner_id=task,
        target_type="document",
        target_id=doc_row["id"],
        added_by="user",
    )
    assert (linked_event.tainted, linked_doc.tainted) == (False, True)
    with pytest.raises(NotFound):
        await link_context(
            workspace.ctx,
            owner_type="task",
            owner_id=task,
            target_type="message",
            target_id=uuid.uuid4(),
            added_by="user",
        )
    with pytest.raises(ValueError, match="url target"):
        await link_context(
            workspace.ctx, owner_type="task", owner_id=task, target_type="url", added_by="user"
        )
