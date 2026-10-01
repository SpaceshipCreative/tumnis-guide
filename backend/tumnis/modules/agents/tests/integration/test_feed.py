"""The dashboard's agent activity feed (P2-17, FR-1.5): `GET /v1/agents/feed` groups the
workspace's task runs as running, waiting on the human, finished and failed, each with its
task and project, newest first."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tumnis.modules.agents.tests.integration._runs import owner_rows, run_world

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-1.5")
@pytest.mark.wp("P2-17")
@pytest.mark.xfail(strict=True, reason="spec:P2-17")
async def test_dashboard_feed_statuses(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-17-03
    Runs in each state land in their group: queued and running in `running`, waiting on
    the human in `waiting`, succeeded in `finished`, and failed, timed out or lost in
    `failed`; each entry names its run, task (with its title) and project. A cancelled
    run shows in none."""
    world = await run_world(fake_runner, workspace, clock)
    states = {
        "running": "Fix footer link",
        "waiting_on_human": "Merge the release branch",
        "succeeded": "Write the changelog",
        "failed": "Rebuild the sitemap",
        "timed_out": "Crawl the old site",
        "cancelled": "Draft the press note",
    }
    runs: dict[str, str] = {}
    for status, title in states.items():
        task = await world.ai_task(title)
        run_id = await world.request(task.id)
        finished = status in {"succeeded", "failed", "timed_out", "cancelled"}
        owner_rows(
            db,
            "UPDATE runs SET status = %s, started_at = now(),"
            " finished_at = CASE WHEN %s THEN now() END WHERE id = %s RETURNING id",
            (status, finished, run_id),
        )
        runs[status] = str(run_id)

    got = await session_client.get("/v1/agents/feed")
    assert got.status_code == 200, got.text
    feed = got.json()

    def ids(group: str) -> set[str]:
        return {entry["run_id"] for entry in feed[group]}

    assert ids("running") == {runs["running"]}
    assert ids("waiting") == {runs["waiting_on_human"]}
    assert ids("finished") == {runs["succeeded"]}
    assert ids("failed") == {runs["failed"], runs["timed_out"]}
    assert runs["cancelled"] not in ids("running") | ids("waiting") | ids("finished") | ids(
        "failed"
    )
    [running] = feed["running"]
    assert running["task_title"] == "Fix footer link"
    assert running["project_id"] == str(world.project_id)
    assert running["status"] == "running"
