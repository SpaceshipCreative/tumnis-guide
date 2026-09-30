"""The repository allow-list (P2-13, FR-12.1): Tumnis reads only the repositories Settings
> GitHub lists; a pull request anywhere else is ignored before anything is stored or
sent."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tumnis.modules.github.tests.integration._github import (
    OPEN_GREEN,
    OPEN_RED,
    new_task,
    rows,
    set_settings,
)

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._pg import DbUrls
    from tests.fixtures import KeyClientFactory, WorkspaceHandle
    from tumnis.modules.github.adapters.fake import FakeGitHubStatus

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-12.1")
@pytest.mark.wp("P2-13")
@pytest.mark.xfail(strict=True, reason="spec:P2-13")
async def test_repos_outside_allow_list_ignored(
    db: DbUrls,
    workspace: WorkspaceHandle,
    key_client: KeyClientFactory,
    github: FakeGitHubStatus,
    dbos: type[DBOS],
) -> None:
    """T-P2-13-08
    With only acme-example/site allowed, linking a brio-example pull request (or a URL
    that is not a github.com pull request) answers 422 and leaves no artifact, no context
    link and no adapter call; opening the task lists nothing and enqueues nothing. The
    allowed repository's pull request is stored (status not read yet) and listed; no
    webhook delivery is recorded along the way.
    """
    await set_settings(workspace.ctx, allowed_repos=["acme-example/site"])
    task = await new_task(workspace.ctx)
    client = await key_client(frozenset({"tasks:read", "tasks:write"}))
    path = f"/v1/tasks/{task.id}/pull-requests"
    async with client:
        outside = await client.post(path, json={"url": OPEN_RED})
        enterprise = await client.post(
            path, json={"url": "https://github.example.com/acme-example/site/pull/42"}
        )
        listed = await client.get(path)
        assert outside.status_code == 422, outside.text
        assert outside.json()["code"] == "repo_not_allowed"
        assert enterprise.status_code == 422, enterprise.text
        assert enterprise.json()["code"] == "not_a_pull_request"
        assert listed.status_code == 200, listed.text
        assert listed.json() == []
        assert rows(db, "SELECT id FROM artifacts") == []
        assert rows(db, "SELECT id FROM context_items") == []
        assert github.calls == []

        allowed = await client.post(path, json={"url": OPEN_GREEN + "/files"})
        again = await client.get(path)
    assert allowed.status_code == 201, allowed.text
    body = allowed.json()
    assert (body["url"], body["repo"], body["number"]) == (OPEN_GREEN, "acme-example/site", 42)
    assert (body["state"], body["checks"], body["review"], body["checked_at"]) == (
        None,
        None,
        None,
        None,
    )
    assert [pr["artifact_id"] for pr in again.json()] == [body["artifact_id"]]
    (artifact,) = rows(db, "SELECT kind, url, external_id FROM artifacts")
    assert artifact == {
        "kind": "pull_request",
        "url": OPEN_GREEN,
        "external_id": "acme-example/site#42",
    }
    assert rows(db, "SELECT id FROM webhook_deliveries") == []
