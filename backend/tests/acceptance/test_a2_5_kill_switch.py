"""A2.5 · Kill switch (phase 2 acceptance, committed red on the phase's first day).

The kill switch pauses dispatch and cancels running runs; resuming releases the held
ones. Turns green with P2-09.

Fixtures: `db`, `dbos`, `clock`, `fakes`, `app`, `workspace`, `fake_runner`,
`session_client`. The plan lists `seed`; `_phase2.arrange_world` makes the rows in the
`workspace` fixture's workspace, which `session_client` and `fake_runner` serve. The two
long-running agents are `_phase2.Streamer`s: a log line every 0.2 s until the runner
receives a `cancel` for the run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tests.acceptance._phase2 import (
    Streamer,
    arrange_world,
    audit,
    relay,
    run_status,
    run_task,
    start_run,
    until,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.req("A2.5", "SAF-4"),
]

CANCEL_WITHIN_S = 5  # plan default


@pytest.mark.wp("P2-09")
async def test_kill_switch_cancels_running_holds_queued_and_resume_releases(  # noqa: PLR0917
    db: DbUrls,
    dbos: Any,
    clock: FixedClock,
    fakes: Any,
    app: FastAPI,
    workspace: WorkspaceHandle,
    fake_runner: FakeRunnerFactory,
    session_client: SessionClient,
) -> None:
    """A2.5
    Given two running runs in project A and a third queued behind them, when the user
    pauses all agents with reason "test", then within 5 s both running runs are
    `cancelled` and the fake runner received a `cancel` for each, the queued run is `held`
    and never started, and a new run request answers 409 `agents_paused`; after resume one
    `killswitch.on` and one `killswitch.off` audit row carry the actor and the reason, and
    the held run starts.
    """
    world = await arrange_world(fake_runner, workspace, clock)
    tasks = [await world.ai_task(f"Long task {n}") for n in (1, 2, 3)]
    other = await world.ai_task("Fix footer link")

    async with relay(db):
        # Two runs start (two per project, SAF-5); the third waits in the queue.
        first, second = [await run_task(session_client, t) for t in tasks[:2]]
        await world.delivered(first)
        await world.delivered(second)
        streamers = [Streamer(world, run).start() for run in (first, second)]
        queued = await run_task(session_client, tasks[2])
        try:
            paused = await session_client.post(
                "/v1/agents/pause", json={"scope": "workspace", "reason": "test"}
            )
            assert paused.status_code in {200, 201, 202}, paused.text

            async def both_cancelled() -> bool:
                return all(
                    [
                        await run_status(session_client, run) == "cancelled"
                        for run in (first, second)
                    ]
                )

            assert await until(both_cancelled, timeout=CANCEL_WITHIN_S)
            assert len(world.cancels(first)) == 1
            assert len(world.cancels(second)) == 1
        finally:
            for streamer in streamers:
                streamer.stop()

        assert await run_status(session_client, queued) == "held"
        assert world.packets(queued) == []
        refused = await start_run(session_client, other)
        assert refused.status_code == 409
        assert refused.json()["code"] == "agents_paused"

        resumed = await session_client.post(
            "/v1/agents/resume", json={"scope": "workspace", "reason": "test over"}
        )
        assert resumed.status_code in {200, 202, 204}, resumed.text

        async def released() -> bool:
            return await run_status(session_client, queued) == "running"

        assert await until(released)
        await world.delivered(queued)
        assert len(world.packets(queued)) == 1

    [on] = audit(db, "killswitch.on")
    [off] = audit(db, "killswitch.off")
    for row, reason in ((on, "test"), (off, "test over")):
        assert row["actor_type"] == "user"
        assert row["actor_id"] == workspace.user_id
        assert row["reason"] == reason
