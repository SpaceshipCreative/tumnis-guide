"""Full-text knowledge search (P1-17, FR-15.3): `GET /v1/knowledge/search?q=` over the
current versions' chunks, citing document, heading path and page; the workspace knowledge
base is shared by every project; row-level security keeps workspaces apart."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.knowledge.tests._samples import fixture_bytes
from tumnis.modules.knowledge.tests.integration._upload import rows, settled, upload

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tumnis.modules.knowledge.tests.integration.conftest import ExtractEnv

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _hits(found: Any) -> list[dict[str, Any]]:
    return list(found["items"] if isinstance(found, dict) else found)


@pytest.mark.req("FR-15.3", "FR-15.1")
@pytest.mark.wp("P1-17")
async def test_search_cites_document_and_page(
    extract_env: ExtractEnv,
    dbos: type[DBOS],
    session_client: SessionClient,
    db: DbUrls,
) -> None:
    """T-P1-17-10
    Given the rate card uploaded to the project and extracted (the stored chunks of the
    A1.5 PDF), a workspace knowledge-base entry that mentions a senior colleague and a
    designer, and an entry about senior designers in another workspace: the query
    `senior designer` for the project returns the rate card's table chunk first, with the
    document's title, the heading path ending in `Rates` and page 2; the workspace entry is
    among the hits; the other workspace's entry never is, and its own search never finds
    this workspace's items.
    """
    from tests.fixtures import WorkspaceHandle, make_workspace  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    env = extract_env
    accepted = await upload(
        session_client, env.project_id, "rate-card-table.pdf", fixture_bytes("rate-card-table.pdf")
    )
    assert accepted.status_code == 202, accepted.text
    doc = await settled(session_client, accepted.json()["id"])
    assert doc["status"] == "ready"
    [table] = rows(
        db,
        "SELECT c.id FROM chunks c JOIN documents d ON d.current_version_id = c.document_version_id"
        " WHERE d.id = %s AND c.text LIKE '%%Senior designer%%'",
        doc["id"],
    )
    shared = await session_client.post(
        "/v1/knowledge/documents/text",
        json={"title": "Hiring", "body_md": "Ask a senior colleague before you hire a designer."},
    )
    assert shared.status_code == 201, shared.text
    # Another workspace made here, not with `two_workspaces`: with that fixture,
    # `session_client` signs in to its workspace B instead of the project's workspace.
    other_id = make_workspace(db, "Other")
    other = WorkspaceHandle(other_id, "Other", WorkspaceContext(other_id, SYSTEM_ACTOR))
    async with tenant_session(other.ctx) as s:
        theirs = await knowledge.create_text_entry(
            s, None, "Their rates", "Senior designer: 90 an hour."
        )

    found = await session_client.get(
        "/v1/knowledge/search", params={"q": "senior designer", "project_id": str(env.project_id)}
    )

    assert found.status_code == 200, found.text
    hits = _hits(found.json())
    assert hits, "no search results"
    first = hits[0]
    assert first["chunk_id"] == str(table["id"])
    assert first["document_id"] == doc["id"]
    assert first["document_title"] == doc["title"]
    assert first["heading_path"][-1] == "Rates"
    assert first["page"] == 2
    ids = {h["document_id"] for h in hits}
    assert shared.json()["id"] in ids
    assert str(theirs.id) not in ids

    async with tenant_session(other.ctx) as s:
        their_hits = await knowledge.search_knowledge(s, "senior designer", project_id=None)
    assert {h.document_id for h in their_hits} == {theirs.id}
