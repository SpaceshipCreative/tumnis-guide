"""Context items: linking is idempotent and inherits the target's taint (P0-12, FR-14.2)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.integrations.tests.integration._integrations import (
    item,
    page,
    rows,
    scalar,
)

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-14.2")
@pytest.mark.wp("P0-12")
async def test_link_is_idempotent_and_inherits_taint(
    app_db: DbUrls, workspace: WorkspaceHandle, connection: uuid.UUID
) -> None:
    """T-P0-12-10
    Linking the same target twice returns one row; a tainted message gives a tainted
    context item, an artifact (trusted by default) an untainted one, and a bare URL a
    tainted one.
    """
    from tumnis.modules.integrations.adapters.fake import ScriptedConnector  # noqa: PLC0415
    from tumnis.modules.integrations.api import ingest_page, link_context  # noqa: PLC0415

    await ingest_page(
        workspace.ctx,
        connection,
        ScriptedConnector(),
        page(
            item("msg-1", subject="Invoice"),
            item("pr-1", "artifact", kind="pull_request", state="open"),
        ),
    )
    [message] = rows(app_db, "messages", connection)
    [artifact] = rows(app_db, "artifacts", connection)
    assert message["tainted"] is True
    assert artifact["tainted"] is False
    task = uuid.uuid4()

    first = await link_context(
        workspace.ctx,
        owner_type="task",
        owner_id=task,
        target_type="message",
        target_id=message["id"],
        added_by="user",
    )
    second = await link_context(
        workspace.ctx,
        owner_type="task",
        owner_id=task,
        target_type="message",
        target_id=message["id"],
        added_by="agent",
    )
    assert second.id == first.id
    assert first.tainted is True
    count = "SELECT count(*) FROM context_items WHERE owner_id = %s AND target_type = %s"
    assert scalar(app_db, count, task, "message") == 1

    trusted = await link_context(
        workspace.ctx,
        owner_type="task",
        owner_id=task,
        target_type="artifact",
        target_id=artifact["id"],
        added_by="user",
    )
    assert trusted.tainted is False

    url, url_again = [
        await link_context(
            workspace.ctx,
            owner_type="task",
            owner_id=task,
            target_type="url",
            target_url="https://example.com/brief",
            added_by="user",
        )
        for _ in range(2)
    ]
    assert url_again.id == url.id
    assert url.tainted is True
    assert scalar(app_db, "SELECT count(*) FROM context_items WHERE owner_id = %s", task) == 3
