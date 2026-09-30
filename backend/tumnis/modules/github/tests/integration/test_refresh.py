"""Refreshing pull request status (P2-13, FR-12.1): opening a task returns the stored
status and enqueues one refresh whose result reaches the browser over `/ws`; the poll
refreshes open pull requests only. Polling is the tested path: GitHub cannot reach a
private-network Tumnis."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tumnis.modules.github.tests.integration._github import (
    CLOSED,
    MERGED,
    OPEN_GREEN,
    T0,
    link,
    live_messages,
    new_task,
    outbox,
    set_settings,
    until,
    wait_for_workflows,
)

if TYPE_CHECKING:
    from dbos import DBOS, DBOSClient

    from tests._pg import DbUrls
    from tests.fixtures import KeyClientFactory, WorkspaceHandle
    from tumnis.modules.github.adapters.fake import FakeGitHubStatus

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

REFRESH = "github_refresh_artifact"


@pytest.mark.req("FR-12.1")
@pytest.mark.wp("P2-13")
async def test_refresh_on_open_enqueues_and_updates(  # noqa: PLR0917
    db: DbUrls,
    workspace: WorkspaceHandle,
    key_client: KeyClientFactory,
    github: FakeGitHubStatus,
    dbos: type[DBOS],
    dbos_client: DBOSClient,
) -> None:
    """T-P2-13-01
    A task links an open pull request whose status was never read. Opening the task twice
    while the first refresh is still running answers the stored status both times and
    enqueues one `github_refresh_artifact` (deduplication id `refresh:<artifact_id>`).
    When it finishes, the task's browsers get `{"entity": "task", "id": <task>}` on the live
    channel, the next open shows state, checks and review, and `artifact.updated` and the
    request counts (`github.fetched`, for the usage totals on /metrics) are in the outbox.
    An open right after a refresh finds the status fresh and enqueues nothing more.
    """
    await set_settings(workspace.ctx, allowed_repos=["acme-example/*"])
    task = await new_task(workspace.ctx)
    await link(workspace.ctx, task.id, OPEN_GREEN)
    client = await key_client(frozenset({"tasks:read"}))
    path = f"/v1/tasks/{task.id}/pull-requests"

    github.pause()
    async with live_messages(db) as live, client:
        first = await client.get(path)
        second = await client.get(path)
        assert first.status_code == 200, first.text
        assert first.json() == second.json()
        (stored,) = first.json()
        assert (stored["state"], stored["checks"], stored["review"]) == (None, None, None)
        github.resume()

        (run,) = await wait_for_workflows(dbos_client, REFRESH)
        assert run.status == "SUCCESS"
        wanted = {"ws": str(workspace.id), "entity": "task", "id": str(task.id)}
        await until(lambda: wanted in live)

        opened = await client.get(path)
        (pr,) = opened.json()
        assert pr["artifact_id"] == stored["artifact_id"]
        assert (pr["state"], pr["checks"], pr["review"]) == ("open", "green", "approved")
        assert (pr["title"], pr["draft"], pr["checked_at"]) == (
            "Add the booking form",
            False,
            "2026-03-09T12:00:00Z",
        )

    runs = await wait_for_workflows(dbos_client, REFRESH)
    assert len(runs) == 1
    assert [op for op, _ in github.calls] == [
        "get_pull",
        "get_combined_status",
        "list_check_runs",
        "list_reviews",
    ]
    (updated,) = outbox(db, "artifact.updated")
    assert updated["payload"]["artifact_id"] == stored["artifact_id"]
    assert updated["payload"]["state"] == "open"
    (fetched,) = outbox(db, "github.fetched")
    assert (fetched["payload"]["requests"], fetched["payload"]["not_modified"]) == (4, 0)


@pytest.mark.req("FR-12.1")
@pytest.mark.wp("P2-13")
async def test_polling_refreshes_open_prs_only(
    app_db: DbUrls,
    workspace: WorkspaceHandle,
    github: FakeGitHubStatus,
    dbos: type[DBOS],
    dbos_client: DBOSClient,
) -> None:
    """T-P2-13-02
    The `github-poll` schedule runs every five minutes. After one read of each, an open, a
    merged and a closed pull request are tracked; the tick refreshes only the open one (its
    second read sends the ETags and GitHub answers 304), and a workspace with the module
    switched off is not polled at all.
    """
    from tumnis.core.modules import set_module_enabled  # noqa: PLC0415
    from tumnis.modules.github import workflows  # noqa: PLC0415

    (tick,) = workflows.schedules()
    assert (tick["schedule_name"], tick["schedule"]) == ("github-poll", "*/5 * * * *")

    await set_settings(workspace.ctx, allowed_repos=["acme-example/site", "cove-example/web"])
    task = await new_task(workspace.ctx)
    tracked = {url: await link(workspace.ctx, task.id, url) for url in (OPEN_GREEN, MERGED, CLOSED)}
    for pr in tracked.values():
        await workflows.refresh(str(workspace.id), str(pr.artifact_id))
    github.calls.clear()

    await workflows.github_poll_tick(T0, None)
    (run,) = await wait_for_workflows(dbos_client, REFRESH)
    assert run.status == "SUCCESS"
    assert {key for _, key in github.calls} == {"acme-example/site#42", _sha_of(github, 42)}
    assert len(github.calls) == 4
    assert github.not_modified == 4

    github.calls.clear()
    await set_module_enabled(workspace.ctx, "github", False)
    await workflows.github_poll_tick(T0, None)
    after = await wait_for_workflows(dbos_client, REFRESH)
    assert [w.workflow_id for w in after] == [run.workflow_id]
    assert github.calls == []


def _sha_of(github: FakeGitHubStatus, number: int) -> str:
    return github.pulls[("acme-example", "site", number)].head.sha
