"""Who writes a text entry decides its trust (P1-17, FR-15.5, SAF-1): a person's session
writes trusted text; an API key (an agent) writes untrusted text labeled `agent`, and its
edit of a person's entry leaves that entry untrusted until a person marks it trusted."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._auth import SessionClient
    from tests.fixtures import KeyClientFactory
    from tumnis.modules.knowledge.tests.integration.conftest import ExtractEnv

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-15.5", "SAF-1")
@pytest.mark.wp("P1-17")
async def test_text_trust_follows_the_caller(
    extract_env: ExtractEnv,
    dbos: type[DBOS],
    session_client: SessionClient,
    key_client: KeyClientFactory,
) -> None:
    """An API key's text entry is untrusted and labeled `agent`; its edit of a person's
    trusted entry leaves it untrusted, and a person's later edit does not restore trust."""
    key = await key_client(["knowledge:write", "context:read"])
    project = str(extract_env.project_id)

    by_key = await key.post(
        "/v1/knowledge/documents/text",
        json={"project_id": project, "title": "Agent notes", "body_md": "From a key."},
    )
    assert by_key.status_code == 201, by_key.text
    assert (by_key.json()["trust"], by_key.json()["label"]) == ("untrusted", "agent")

    by_person = await session_client.post(
        "/v1/knowledge/documents/text",
        json={"project_id": project, "title": "Kickoff", "body_md": "Logo first."},
    )
    assert by_person.status_code == 201, by_person.text
    note = by_person.json()
    assert note["trust"] == "trusted"

    edited = await key.patch(
        f"/v1/knowledge/documents/{note['id']}",
        json={"version": note["version"], "body_md": "Logo last."},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["trust"] == "untrusted"

    again = await session_client.patch(
        f"/v1/knowledge/documents/{note['id']}",
        json={"version": edited.json()["version"], "body_md": "Logo first after all."},
    )
    assert again.status_code == 200, again.text
    assert again.json()["trust"] == "untrusted", "only marking it trusted restores trust"
