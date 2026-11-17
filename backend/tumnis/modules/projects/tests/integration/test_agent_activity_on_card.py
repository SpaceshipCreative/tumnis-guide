"""A project's last agent activity (FR-1.1): the dashboard card's "Agent active ..." line
reads `last_agent_activity_at` from the project, which nothing has filled since P0-17
declared it ("always None until P2-04")."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.agents.tests.integration._human import started
from tumnis.modules.agents.tests.integration._runs import relay, run_world, wait_until

if TYPE_CHECKING:
    from dbos import DBOS
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]


@pytest.mark.req("FR-1.1")
@pytest.mark.wp("P0-17")
async def test_project_shows_when_its_agent_was_last_active(  # noqa: PLR0917
    app: FastAPI,
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """FIX-project-activity-01
    Given two projects and an agent run that started in one of them, `GET /v1/projects`
    and `GET /v1/projects/{id}` give that project a `last_agent_activity_at` timestamp,
    and the project no agent has worked in keeps null.
    """
    world = await run_world(fake_runner, workspace, clock, others=(("Beta app", "beta-app"),))
    acme, beta = str(world.project_id), str(world.projects["Beta app"])
    task = await world.ai_task("Fix footer link")

    async def activity() -> dict[str, str | None]:
        listed = await session_client.get("/v1/projects")
        assert listed.status_code == 200, listed.text
        return {p["id"]: p["last_agent_activity_at"] for p in listed.json()["items"]}

    async with relay(db):
        await started(world, db, task.id)
        seen: dict[str, str | None] = {}

        async def shown() -> bool:
            seen.update(await activity())
            return seen[acme] is not None

        assert await wait_until(shown, timeout=10, every=0.5), seen

    assert seen[beta] is None
    assert datetime.fromisoformat(str(seen[acme])).tzinfo is not None
    one = await session_client.get(f"/v1/projects/{acme}")
    assert one.status_code == 200, one.text
    assert one.json()["last_agent_activity_at"] == seen[acme]
