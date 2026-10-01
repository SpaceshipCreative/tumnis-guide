"""The kill switch stops skill runs too (P2-09 PR 2, SAF-4, Scott decision 40): a
`run_skill` run (P1-04's enrichment and planning runs) in a pause's scope is cancelled
through the adapter, and none is dispatched while the pause holds, the same as a
`dispatch_run` run. A person's Stop on a running skill run ends it `cancelled` too.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from tumnis.modules.agents.tests.contract.base import make_packet
from tumnis.modules.agents.tests.integration._runs import (
    cancels,
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

OUTCOME_WITHIN_S = 20


def _run(db: DbUrls, run_id: UUID) -> tuple[str, str | None] | None:
    rows = owner_rows(db, "SELECT status, stop_reason FROM runs WHERE id = %s", (run_id,))
    return (rows[0][0], rows[0][1]) if rows else None


def _status(db: DbUrls, run_id: UUID) -> str | None:
    found = _run(db, run_id)
    return None if found is None else found[0]


def _profile_of(db: DbUrls, project_id: UUID) -> UUID:
    [(profile_id,)] = owner_rows(
        db,
        "SELECT id FROM agent_profiles WHERE project_id = %s AND deleted_at IS NULL",
        (project_id,),
    )
    return UUID(str(profile_id))


def _sent_runs(db: DbUrls, run_id: UUID) -> int:
    [(count,)] = owner_rows(
        db,
        "SELECT count(*) FROM runner_messages WHERE direction = 'out' AND type = 'run'"
        " AND payload->>'run_id' = %s",
        (str(run_id),),
    )
    return int(count)


@pytest.mark.req("SAF-4")
@pytest.mark.wp("P2-09")
async def test_kill_switch_cancels_a_running_skill_run(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-09-12
    An enrichment run (`run_skill`) is running; its runner never answers. Pausing every
    agent counts it among the cancelled runs, its runner gets one `cancel`, and its
    workflow ends it `cancelled` with stop reason `killswitch`.
    """
    from tumnis.modules.agents import workflows  # noqa: PLC0415

    world = await run_world(fake_runner, workspace, clock)
    packet = make_packet(world.profile_id)
    async with relay(db):
        handle = await workflows.start_run_skill(workspace.id, packet)
        assert await wait_until(lambda: _status(db, packet.run_id) == "running")

        paused = await session_client.post(
            "/v1/agents/pause", json={"scope": "workspace", "reason": "Agents misbehave"}
        )
        assert paused.status_code == 200, paused.text
        assert paused.json()["cancelled_runs"] == 1

        outcome = await asyncio.wait_for(handle.get_result(), OUTCOME_WITHIN_S)
        assert outcome["status"] == "cancelled"
        assert outcome["error"] == "killswitch"
        assert _run(db, packet.run_id) == ("cancelled", "killswitch")
        assert await wait_until(lambda: cancels(world.runner, packet.run_id) != [])
        assert len(cancels(world.runner, packet.run_id)) == 1


@pytest.mark.req("SAF-4")
@pytest.mark.wp("P2-09")
async def test_no_skill_run_is_dispatched_while_paused(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-09-13
    While every agent is paused, a new enrichment run is never sent to its runner: its
    workflow ends it `cancelled` with `killswitch` at once, not `failed`.
    """
    from tumnis.modules.agents import workflows  # noqa: PLC0415

    world = await run_world(fake_runner, workspace, clock)
    paused = await session_client.post(
        "/v1/agents/pause", json={"scope": "workspace", "reason": "Agents misbehave"}
    )
    assert paused.status_code == 200, paused.text

    packet = make_packet(world.profile_id)
    handle = await workflows.start_run_skill(workspace.id, packet)
    outcome = await asyncio.wait_for(handle.get_result(), OUTCOME_WITHIN_S)
    assert outcome["status"] == "cancelled"
    assert outcome["error"] == "killswitch"
    await asyncio.sleep(0.5)
    assert _sent_runs(db, packet.run_id) == 0
    assert all(run.run_id != packet.run_id for run in world.runner.runs())


@pytest.mark.req("SAF-4")
@pytest.mark.wp("P2-09")
async def test_project_pause_cancels_only_that_projects_skill_run(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-09-14
    Project A ("Acme site") and project B ("Beacon app") each have a running enrichment
    run. Pausing A cancels A's run through the adapter (stop reason `project_paused`);
    B's run keeps running with no `cancel`.
    """
    from tumnis.modules.agents import workflows  # noqa: PLC0415

    world = await run_world(fake_runner, workspace, clock, others=(("Beacon app", "beacon-app"),))
    acme, beacon = world.projects["Acme site"], world.projects["Beacon app"]
    acme_run, beacon_run = make_packet(world.profile_id), make_packet(_profile_of(db, beacon))
    async with relay(db):
        acme_handle = await workflows.start_run_skill(workspace.id, acme_run)
        await workflows.start_run_skill(workspace.id, beacon_run)
        assert await wait_until(
            lambda: _status(db, acme_run.run_id) == _status(db, beacon_run.run_id) == "running"
        )

        paused = await session_client.post(
            f"/v1/projects/{acme}/pause", json={"reason": "Acme's agent misbehaves"}
        )
        assert paused.status_code == 200, paused.text
        assert paused.json()["cancelled_runs"] == 1

        outcome = await asyncio.wait_for(acme_handle.get_result(), OUTCOME_WITHIN_S)
        assert outcome["status"] == "cancelled"
        assert outcome["error"] == "project_paused"
        assert _run(db, acme_run.run_id) == ("cancelled", "project_paused")
        assert await wait_until(lambda: cancels(world.runner, acme_run.run_id) != [])
        assert len(cancels(world.runner, acme_run.run_id)) == 1

        await asyncio.sleep(1)
        assert _run(db, beacon_run.run_id) == ("running", None)
        assert cancels(world.runner, beacon_run.run_id) == []


@pytest.mark.req("SAF-4", "FR-5.5")
@pytest.mark.wp("P2-09")
@pytest.mark.xfail(strict=True, reason="spec:P2-09")
async def test_stop_ends_a_running_skill_run_cancelled(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-09-15
    Regression: a person's Stop on a running enrichment run used to end it `failed` with
    error "None". Its runner now gets one `cancel`, and the run ends `cancelled` with stop
    reason `stopped_by_user`.
    """
    from tumnis.modules.agents import workflows  # noqa: PLC0415

    world = await run_world(fake_runner, workspace, clock)
    packet = make_packet(world.profile_id)
    async with relay(db):
        handle = await workflows.start_run_skill(workspace.id, packet)
        assert await wait_until(lambda: _status(db, packet.run_id) == "running")

        stopped = await session_client.post(f"/v1/runs/{packet.run_id}/cancel", json={})
        assert stopped.status_code == 202, stopped.text

        outcome = await asyncio.wait_for(handle.get_result(), OUTCOME_WITHIN_S)
        assert outcome["status"] == "cancelled"
        assert outcome["error"] == "stopped_by_user"
        assert _run(db, packet.run_id) == ("cancelled", "stopped_by_user")
        assert await wait_until(lambda: cancels(world.runner, packet.run_id) != [])
        assert len(cancels(world.runner, packet.run_id)) == 1
