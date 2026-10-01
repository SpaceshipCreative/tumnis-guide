"""A text entry carries its writer's taint (P1-17 review follow-up, SAF-1, R-31, P2-08):
what an API key with no run writes is tainted, as `add_document` already does for agent
documents, so the entry cannot reach a packet as clean text; its edit of a person's entry
taints that entry too, and nothing lowers it again."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._auth import SessionClient
    from tests.fixtures import KeyClientFactory
    from tumnis.modules.knowledge.tests.integration.conftest import ExtractEnv

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("SAF-1", "FR-15.5")
@pytest.mark.wp("P1-17")
async def test_text_taint_follows_the_caller(
    extract_env: ExtractEnv,
    dbos: type[DBOS],
    session_client: SessionClient,
    key_client: KeyClientFactory,
) -> None:
    """A key's entry is tainted; a person's is not until a key edits it; a person's later
    edit keeps the taint."""
    key = await key_client(["knowledge:write", "context:read"])
    project = str(extract_env.project_id)

    by_key = await key.post(
        "/v1/knowledge/documents/text",
        json={"project_id": project, "title": "Agent notes", "body_md": "From a key."},
    )
    assert by_key.status_code == 201, by_key.text
    assert by_key.json()["tainted"] is True

    by_person = await session_client.post(
        "/v1/knowledge/documents/text",
        json={"project_id": project, "title": "Kickoff", "body_md": "Logo first."},
    )
    assert by_person.status_code == 201, by_person.text
    note = by_person.json()
    assert note["tainted"] is False

    edited = await key.patch(
        f"/v1/knowledge/documents/{note['id']}",
        json={"version": note["version"], "body_md": "Logo last."},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["tainted"] is True

    again = await session_client.patch(
        f"/v1/knowledge/documents/{note['id']}",
        json={"version": edited.json()["version"], "body_md": "Logo first after all."},
    )
    assert again.status_code == 200, again.text
    assert again.json()["tainted"] is True
