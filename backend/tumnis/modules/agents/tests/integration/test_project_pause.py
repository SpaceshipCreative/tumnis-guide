"""A project pause (P2-09, SAF-4): the kill switch for one project. Its running runs are
cancelled through the adapter and new runs refused; other projects carry on.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from tumnis.modules.agents.tests.integration._runs import (
    audit_rows,
    cancels,
    open_pauses,
    owner_rows,
    relay,
    run_world,
    wait_until,
)

if TYPE_CHECKING:
    from dbos import DBOS

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


def _run(db: DbUrls, run_id: UUID) -> tuple[str, str | None]:
    [(status, reason)] = owner_rows(
        db, "SELECT status, stop_reason FROM runs WHERE id = %s", (run_id,)
    )
    return status, reason


@pytest.mark.req("SAF-4")
@pytest.mark.wp("P2-09")
async def test_project_pause_affects_only_that_project(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-09-07
    Project A ("Acme site") and project B ("Beacon app") each have a running run. Pausing
    A cancels A's run through the adapter (reason `project_paused`) and refuses a new Run
    in A with 409 `agents_paused`; B's run keeps running with no `cancel`, and a new Run in
    B is accepted. The pause is audited as `project.paused` naming the project; resuming
    A lets it run again and is audited as `project.resumed`.
    """
    world = await run_world(fake_runner, workspace, clock, others=(("Beacon app", "beacon-app"),))
    acme, beacon = world.projects["Acme site"], world.projects["Beacon app"]
    a_task = await world.ai_task("Acme footer")
    b_task = await world.ai_task("Beacon login", project_id=beacon)
    a_next = await world.ai_task("Acme header")
    b_next = await world.ai_task("Beacon logout", project_id=beacon)
    async with relay(db):
        a_run = await world.request(a_task.id)
        b_run = await world.request(b_task.id)
        assert await wait_until(
            lambda: _run(db, a_run)[0] == _run(db, b_run)[0] == "running", timeout=5
        )

        paused = await session_client.post(
            f"/v1/projects/{acme}/pause", json={"reason": "Acme's agent misbehaves"}
        )
        assert paused.status_code in {200, 201, 202}, paused.text
        assert paused.json()["cancelled_runs"] == 1
        assert open_pauses(db) == [("project", acme, "Acme's agent misbehaves")]

        assert await wait_until(lambda: _run(db, a_run)[0] == "cancelled", timeout=5)
        assert _run(db, a_run) == ("cancelled", "project_paused")
        assert len(cancels(world.runner, a_run)) == 1

        refused = await session_client.post(f"/v1/tasks/{a_next.id}/run", json={})
        assert refused.status_code == 409, refused.text
        assert refused.json()["code"] == "agents_paused"

        await asyncio.sleep(1)
        assert _run(db, b_run) == ("running", None)
        assert cancels(world.runner, b_run) == []
        accepted = await session_client.post(f"/v1/tasks/{b_next.id}/run", json={})
        assert accepted.status_code == 202, accepted.text

        resumed = await session_client.post(
            f"/v1/projects/{acme}/resume", json={"reason": "Fixed its profile"}
        )
        assert resumed.status_code in {200, 202, 204}, resumed.text
        assert open_pauses(db) == []
        again = await session_client.post(f"/v1/tasks/{a_next.id}/run", json={})
        assert again.status_code == 202, again.text

    [on] = audit_rows(db, "project.paused")
    assert on[0] == "user"
    assert on[2] == "Acme's agent misbehaves"
    assert (on[5], on[6]) == ("project", acme)
    [off] = audit_rows(db, "project.resumed")
    assert off[2] == "Fixed its profile"
    assert (off[5], off[6]) == ("project", acme)
    assert audit_rows(db, "killswitch.on") == []
