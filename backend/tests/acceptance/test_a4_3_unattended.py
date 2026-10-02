"""A4.3 · Unattended window runs green-light untainted tasks, refuses tainted ones, day close
shows the queue (phase 4 acceptance; turns green with P4-04).

The phase 4 suite (P4-00) was never committed, so P4-04 adds this test red with its own
spec tests, from the plan's A4.3 text.

Fixtures: `db`, `dbos`, `clock`, `fakes`, `app`, `workspace`, `fake_runner`,
`session_client`. The plan lists the seed; `_phase2.arrange_world` makes the plan's rows in
the `workspace` fixture's workspace (America/New_York), the one `session_client` and
`fake_runner` serve (Scott decision 37 keeps acceptance seed data to the SEED work package).
U3's paused project is "Beta app" (U1 runs in "Acme site", which stays running). The test
plays the agent: it posts U1's result over MCP with the run's task token, ten fake minutes
in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tests.acceptance._phase2 import (
    MASTER,
    arrange_world,
    post_result,
    relay,
    review_items,
    until,
)
from tests.acceptance._unattended import (
    DAY,
    EVENING,
    LATER,
    NIGHT,
    RELEASE,
    RELEASE_TICK,
    UNATTENDED_TICK,
    arrange_night,
    at,
    attempts,
    fire,
    notifications,
    notify_runs,
    runs_of,
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
    pytest.mark.req("FR-4.5", "J7", "SAF-1"),
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]


@pytest.mark.wp("P4-04")
@pytest.mark.xfail(strict=True, reason="spec:P4-04")
async def test_window_runs_green_light_untainted_only(  # noqa: PLR0917
    db: DbUrls,
    dbos: Any,
    clock: FixedClock,
    fakes: Any,
    app: FastAPI,
    workspace: WorkspaceHandle,
    fake_runner: FakeRunnerFactory,
    session_client: SessionClient,
) -> None:
    """A4.3
    With a weekday 22:00 to 06:00 window, U1 (green), U2 (tainted) and U3 (paused project)
    queued and H1 (Human) refused at queue time: the 22:05 tick requests exactly one run,
    U1's, unattended; U2 and U3 get no run and a review item each saying why. U1's result at
    22:15 reaches no channel (batched overnight); at 08:45 one notification summarizing "1
    result from overnight" goes out, and the review queue holds U1's result as overnight.
    """
    world = await arrange_world(fake_runner, workspace, clock, master=True)
    world.runner.script(MASTER, "focus", {"message": "1 result from overnight."})
    await at(session_client, EVENING)
    night = await arrange_night(world, session_client)
    assert night.h1_queued.status_code == 422
    assert night.h1_queued.json()["code"] == "not_ai"

    async with relay(db):
        # 2. 22:05: the tick.
        await at(session_client, NIGHT)
        fired = await fire(session_client, UNATTENDED_TICK)
        assert fired.status_code == 200, fired.text

        # 3. Exactly one run, U1's, requested unattended.
        [run] = runs_of(db, night.u1)
        assert runs_of(db, night.u2) == []
        assert runs_of(db, night.u3) == []
        [started] = [
            e["payload"] for e in (await _requested(db)) if e["payload"]["task_id"] == str(night.u1)
        ]
        assert started["unattended"] is True
        refused = {
            item["target_id"]: item
            for item in await review_items(session_client, "unattended_refused")
        }
        assert set(refused) == {str(night.u2), str(night.u3)}
        assert refused[str(night.u2)]["payload"]["reason"] == "From outside content: needs you"
        assert refused[str(night.u3)]["payload"]["reason"] == "Project paused"

        # 4. 22:15: U1's result is posted; nothing is delivered, the item is batched.
        await world.delivered(run["id"])
        await at(session_client, LATER)
        outcome = await post_result(
            world.runner, world.token(run["id"]), run["id"], "Fixed the footer link"
        )
        assert outcome.ok, outcome
        [result] = await until(lambda: review_items(session_client, "result"))
        assert result["payload"]["batch"] == "overnight"
        assert notify_runs(db, workspace) == []
        assert attempts(db, workspace) == []

        # 5. 08:45: one summary notification goes out.
        await at(session_client, RELEASE)
        released = await fire(session_client, RELEASE_TICK)
        assert released.status_code == 200, released.text
        assert await until(lambda: len(notify_runs(db, workspace)) == 1, timeout=30)
        summaries = [n for n in notifications(db, workspace) if n["kind"] == "batch"]
        assert len(summaries) == 1
        assert "1 result from overnight" in summaries[0]["payload"]["title"]
        assert await until(
            lambda: [a["status"] for a in attempts(db, workspace)] == ["sent"], timeout=30
        )
        [overnight] = await review_items(session_client, "result")
        assert overnight["target_id"] == str(night.u1)
        assert overnight["payload"]["batch"] == "overnight"


async def _requested(db: DbUrls) -> list[dict[str, Any]]:
    from tests.acceptance._phase2 import rows  # noqa: PLC0415

    return rows(db, "SELECT payload FROM outbox WHERE name = 'run.requested' ORDER BY id")


@pytest.mark.wp("P4-04")
@pytest.mark.xfail(strict=True, reason="spec:P4-04")
async def test_day_close_lists_queued_unattended(  # noqa: PLR0917
    db: DbUrls,
    dbos: Any,
    clock: FixedClock,
    fakes: Any,
    app: FastAPI,
    workspace: WorkspaceHandle,
    fake_runner: FakeRunnerFactory,
    session_client: SessionClient,
) -> None:
    """A4.3
    At 21:50 the day close (`GET /v1/day/{date}/summary`) lists U1, U2 and U3 in
    `queued_unattended`; U1 will run, U2 and U3 are marked "will not run" with their reasons
    ("From outside content: needs you", "Project paused"). H1 was never queued.
    """
    world = await arrange_world(fake_runner, workspace, clock)
    await at(session_client, EVENING)
    night = await arrange_night(world, session_client)

    answer = await session_client.get(f"/v1/day/{DAY}/summary")

    assert answer.status_code == 200, answer.text
    listed = {item["task_id"]: item for item in answer.json()["queued_unattended"]}
    assert set(listed) == {str(night.u1), str(night.u2), str(night.u3)}
    assert listed[str(night.u1)]["will_run"] is True
    assert listed[str(night.u2)]["will_run"] is False
    assert listed[str(night.u2)]["reason"] == "From outside content: needs you"
    assert listed[str(night.u3)]["will_run"] is False
    assert listed[str(night.u3)]["reason"] == "Project paused"
