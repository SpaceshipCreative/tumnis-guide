"""The signed webhook endpoint (P2-13, SEC-5), built for when a relay forwards GitHub's
deliveries: HMAC only (no session or key), each delivery accepted once."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.github.tests.integration._github import (
    OPEN_GREEN,
    WEBHOOK_SECRET,
    link,
    new_task,
    rows,
    set_settings,
    wait_for_workflows,
)

if TYPE_CHECKING:
    import httpx
    from dbos import DBOS, DBOSClient

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.modules.github.adapters.fake import FakeGitHubStatus

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

DELIVERY = "72d3162e-cc78-11e3-81ab-4c9367dc0958"  # GitHub's documented example id


def _headers(body: bytes, delivery: str, secret: str = WEBHOOK_SECRET) -> dict[str, str]:
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": f"sha256={signature}",
        "User-Agent": "GitHub-Hookshot/0000000",
    }


@pytest.mark.req("SEC-5")
@pytest.mark.wp("P2-13")
@pytest.mark.xfail(strict=True, reason="spec:P2-13")
async def test_replayed_delivery_refused(  # noqa: PLR0917
    db: DbUrls,
    workspace: WorkspaceHandle,
    client: httpx.AsyncClient,
    github: FakeGitHubStatus,
    dbos: type[DBOS],
    dbos_client: DBOSClient,
) -> None:
    """T-P2-13-04
    Without a webhook secret the endpoint is not there (404). With one, a signed
    `pull_request` delivery for a tracked pull request answers 202 and enqueues one
    refresh; the same `X-GitHub-Delivery` again answers 409 `duplicate_delivery` and
    enqueues nothing. A wrong signature is 401 `invalid_signature` and records nothing.
    """
    task = await new_task(workspace.ctx)
    await set_settings(workspace.ctx, allowed_repos=["acme-example/site"])
    tracked = await link(workspace.ctx, task.id, OPEN_GREEN)
    body = json.dumps(
        {
            "action": "synchronize",
            "number": 42,
            "pull_request": {"number": 42, "html_url": OPEN_GREEN, "state": "open"},
            "repository": {"full_name": "acme-example/site"},
        }
    ).encode()
    path = f"/v1/github/webhook/{workspace.id}"

    off = await client.post(path, content=body, headers=_headers(body, DELIVERY))
    assert off.status_code == 404, off.text

    await set_settings(
        workspace.ctx, allowed_repos=["acme-example/site"], webhook_secret=WEBHOOK_SECRET
    )
    forged = await client.post(
        path, content=body, headers=_headers(body, DELIVERY, secret="guessed-secret")
    )
    assert forged.status_code == 401, forged.text
    assert forged.json()["code"] == "invalid_signature"
    assert rows(db, "SELECT delivery_id FROM webhook_deliveries") == []

    first = await client.post(path, content=body, headers=_headers(body, DELIVERY))
    assert first.status_code == 202, first.text
    (run,) = await wait_for_workflows(dbos_client, "github_refresh_artifact")
    assert run.status == "SUCCESS"

    replayed = await client.post(path, content=body, headers=_headers(body, DELIVERY))
    assert replayed.status_code == 409, replayed.text
    assert replayed.json()["code"] == "duplicate_delivery"

    runs = await wait_for_workflows(dbos_client, "github_refresh_artifact")
    assert [r.workflow_id for r in runs] == [run.workflow_id]
    assert rows(db, "SELECT delivery_id FROM webhook_deliveries") == [{"delivery_id": DELIVERY}]
    assert [op for op, _ in github.calls].count("get_pull") == 1
    (artifact,) = rows(db, "SELECT id, state FROM artifacts")
    assert artifact == {"id": tracked.artifact_id, "state": "open"}
