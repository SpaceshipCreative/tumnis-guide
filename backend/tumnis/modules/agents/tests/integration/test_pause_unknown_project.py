"""A pause or resume naming a project the caller cannot see answers 404 whatever its scope
(P2-09, A0.3): the project is looked up before the scope and project_id are checked
against each other, so another workspace's project id never gets a 422 that tells it
apart from a missing one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

if TYPE_CHECKING:
    from tests._auth import SessionClient
    from tests.fixtures import WorkspaceHandle

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]


@pytest.mark.req("SAF-4")
@pytest.mark.wp("P2-09")
@pytest.mark.parametrize("path", ["/v1/agents/pause", "/v1/agents/resume"])
@pytest.mark.parametrize("scope", ["workspace", "project"])
async def test_unknown_project_is_404_for_either_scope(
    path: str, scope: str, workspace: WorkspaceHandle, session_client: SessionClient
) -> None:
    """A pause or resume with a project_id no project in the caller's workspace has, for
    either scope, is 404 and pauses nothing."""
    del workspace  # the session's workspace
    response = await session_client.post(
        path, json={"scope": scope, "project_id": str(uuid4()), "reason": "Checking"}
    )
    assert response.status_code == 404, response.text
    pauses = await session_client.get("/v1/agents/pause")
    assert pauses.status_code == 200, pauses.text
    assert pauses.json()["workspace"] is None
    assert pauses.json()["projects"] == []
