"""Marking a document trusted can name the version the person reviewed (P1-17 review
follow-up, FR-15.5): an agent's edit in between makes the request 409 `stale_version`
with the current document, so a person never promotes text they have not seen."""

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
async def test_trust_names_the_reviewed_version(
    extract_env: ExtractEnv,
    dbos: type[DBOS],
    session_client: SessionClient,
    key_client: KeyClientFactory,
) -> None:
    """The person reviewed version 1; the key's edit made version 2; trusting version 1
    answers 409 and leaves the entry untrusted; trusting version 2 succeeds."""
    key = await key_client(["knowledge:write", "context:read"])
    made = await key.post(
        "/v1/knowledge/documents/text",
        json={
            "project_id": str(extract_env.project_id),
            "title": "Agent notes",
            "body_md": "First draft.",
        },
    )
    assert made.status_code == 201, made.text
    reviewed = made.json()

    edited = await key.patch(
        f"/v1/knowledge/documents/{reviewed['id']}",
        json={"version": reviewed["version"], "body_md": "Something else."},
    )
    assert edited.status_code == 200, edited.text

    stale = await session_client.post(
        f"/v1/knowledge/documents/{reviewed['id']}/trust",
        json={"trusted": True, "version": reviewed["version"]},
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "stale_version"
    assert stale.json()["current"]["trust"] == "untrusted"

    current = await session_client.post(
        f"/v1/knowledge/documents/{reviewed['id']}/trust",
        json={"trusted": True, "version": edited.json()["version"]},
    )
    assert current.status_code == 200, current.text
    assert current.json()["trust"] == "trusted"
