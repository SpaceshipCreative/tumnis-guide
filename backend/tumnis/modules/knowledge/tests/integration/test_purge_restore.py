"""Purging the knowledge trash does not race a restore (P1-17 review follow-up): the purge
locks the documents it picks and skips any a restore holds, so a document being restored
is never deleted under it."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._auth import SessionClient
    from tumnis.modules.knowledge.tests.integration.conftest import ExtractEnv

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-15.6")
@pytest.mark.wp("P1-17")
async def test_purge_skips_a_document_being_restored(
    extract_env: ExtractEnv, dbos: type[DBOS], session_client: SessionClient
) -> None:
    """While a restore holds a trashed document's row, a purge leaves it alone (no wait,
    no delete); once the restore commits, the document is back with its body."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    made = await session_client.post(
        "/v1/knowledge/documents/text",
        json={"project_id": str(extract_env.project_id), "title": "Rates", "body_md": "Kept."},
    )
    assert made.status_code == 201, made.text
    doc_id = UUID(made.json()["id"])
    trashed = await session_client.delete(f"/v1/knowledge/documents/{doc_id}")
    assert trashed.status_code == 204, trashed.text

    async with tenant_session(extract_env.ws.ctx) as restoring:
        await knowledge.restore(restoring, doc_id)
        async with tenant_session(extract_env.ws.ctx) as purging:
            await purging.execute(text("SET LOCAL lock_timeout = '2s'"))
            purged = await knowledge.purge_trash(
                purging, datetime(9999, 1, 1, tzinfo=UTC), limit=100
            )
        assert purged == 0

    got = await session_client.get(f"/v1/knowledge/documents/{doc_id}")
    assert got.status_code == 200, got.text
    assert got.json()["body_md"] == "Kept."
