"""The acceptance runtime (SEED impl-2, Scott decision 37; R-37): what the compose.test
stack needs so the phase 1 and phase 2 journeys can run against the acceptance set.

compose.test has no runner daemon. With fake adapters and the fake-script store on (the
api's lifespan and the worker's `main` turn it on), a daemon profile whose runner never
connected is served by the in-process FakeAgent:

- the master and the project agents read as `ready` (`master_agent`, `agent_for_project`),
  so the planner asks the master and enrichment asks the project agent;
- a `run_skill` dispatch (plan, enrich) plays the stored `<profile>/<skill>` script: the
  named recording, fitted to the packet (`title:` picks, the sentinel task id), answered to
  the waiting workflow after the script's delay;
- `POST /v1/test/tick/planner-tick` runs one planner tick at the server clock's time and
  returns once the morning plans it started are published.

Here the app runs in-process on the test database (the `client` fixture, whose transport
runs no lifespan: the tests turn the store on themselves) with the worker's DBOS in the
same process (`dbos`).
"""

from __future__ import annotations

import asyncio
import importlib
import time
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest

if TYPE_CHECKING:
    import httpx
    from dbos import DBOS

    from tests._pg import DbUrls

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.wp("SEED"),
]

# Every module's workflows and subscribers, registered before DBOS launches.
importlib.import_module("tumnis.wiring")

MONDAY = date(2026, 3, 9)
ACCEPTANCE = {"set": "acceptance", "anchor": MONDAY.isoformat()}
MONDAY_PLAN_TIME = "2026-03-09T12:30:00Z"  # 08:30 in New York, the default plan time
NOW = datetime(2026, 3, 9, 12, 30, tzinfo=UTC)
MONDAY_PICKS = [
    "Invoice Acme for phase one",
    "Send logo drafts to Acme",
    "Record lesson one",
    "Generate March analytics report",
]
SETTLE_S = 30.0
SILENT_END_S = 10.0  # well under the 90 s run timeout a silent fake would otherwise hold


def _rows(db: DbUrls, query: str, *params: Any) -> list[dict[str, Any]]:
    import psycopg  # noqa: PLC0415
    from psycopg.rows import dict_row  # noqa: PLC0415

    from tests._pg import OWNER  # noqa: PLC0415

    with psycopg.connect(db.libpq(OWNER), row_factory=dict_row) as conn:
        return list(conn.execute(query.encode(), params).fetchall())


async def _until(check: Callable[[], Any], timeout_s: float = SETTLE_S) -> Any:
    deadline = time.monotonic() + timeout_s
    while True:
        value = check()
        if value or time.monotonic() >= deadline:
            return value
        await asyncio.sleep(0.1)


@pytest.fixture
def script_store() -> Iterator[None]:
    """The fake-script store on, as the compose.test api and worker have it."""
    from tumnis.core import fake_scripts  # noqa: PLC0415

    fake_scripts.enable()
    try:
        yield
    finally:
        fake_scripts.disable()


async def _reset(client: httpx.AsyncClient) -> None:
    reset = await client.post("/v1/test/reset", params=ACCEPTANCE)
    assert reset.status_code == 204, reset.text


async def _script(
    client: httpx.AsyncClient, profile: str, skill: str, result: str, **more: Any
) -> None:
    body = {"profile": profile, "skill": skill, "result": f"{result}.result.json", **more}
    scripted = await client.post("/v1/test/fakes/runner/script", json=body)
    assert scripted.status_code == 204, scripted.text


def _ctx(db: DbUrls) -> Any:
    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415

    [workspace] = _rows(db, "SELECT id FROM workspaces")
    return WorkspaceContext(workspace["id"], SYSTEM_ACTOR)


def _project(db: DbUrls, name: str) -> UUID:
    [project] = _rows(db, "SELECT id FROM projects WHERE name = %s", name)
    return UUID(str(project["id"]))


@pytest.mark.req("A1.2", "A1.1", "A2.6")
async def test_agents_ready_in_fakes_mode_until_their_runner_connects(
    client: httpx.AsyncClient, db: DbUrls
) -> None:
    """T-SEED-17
    In the compose.test shape (fakes, the store on), the acceptance master and project
    agents, whose runner `homelab-hermes` never connected, read as ready. With the store off
    (a unit or integration run that only selects fakes) they stay offline, as before; once
    the runner has connected (a heartbeat on record) its own status decides again."""
    from tumnis.core import fake_scripts  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415

    await _reset(client)
    ctx, acme = _ctx(db), _project(db, "Acme site")

    async def states() -> tuple[str, str]:
        master = await agents.master_agent(ctx=ctx)
        project = await agents.agent_for_project(acme, now=NOW, ctx=ctx)
        return master.availability, project

    assert await states() == ("offline", "offline")  # the store is off
    fake_scripts.enable()
    try:
        assert await states() == ("ready", "ready")
        _rows(db, "UPDATE runners SET last_heartbeat_at = now() - interval '1 hour' RETURNING id")
        assert await states() == ("offline", "offline")  # connected once: judged as usual
    finally:
        fake_scripts.disable()


@pytest.mark.req("A1.2", "A2.6")
async def test_planner_tick_publishes_the_masters_scripted_plan(
    client: httpx.AsyncClient, db: DbUrls, dbos: type[DBOS], script_store: None
) -> None:
    """T-SEED-18
    J1's arrangement: the acceptance set on Monday 2026-03-09, the master scripted with
    `plan__monday_four_picks`, the server clock at 08:30 New York time, one
    `POST /v1/test/tick/planner-tick`. When the tick answers, Monday's morning plan is
    published from the master (no notice), its four items are the recording's picks in
    order (their `title:` names resolved to the acceptance tasks), and the master's run
    ended succeeded with its `dispatched` and `result` events. A second tick the same
    morning builds nothing more."""
    del dbos
    await _reset(client)
    await _script(client, "tumnis-master", "plan", "plan__monday_four_picks")
    clock = await client.post("/v1/test/clock", json={"time": MONDAY_PLAN_TIME})
    assert clock.status_code == 200, clock.text

    tick = await client.post("/v1/test/tick/planner-tick")
    assert tick.status_code == 200, tick.text
    assert tick.json() == {"woken": 1}

    plans = _rows(
        db,
        "SELECT id, source, trigger, status, notice, master_run_id FROM daily_plans WHERE day = %s",
        MONDAY,
    )
    assert len(plans) == 1
    [plan] = plans
    assert (plan["source"], plan["trigger"], plan["status"], plan["notice"]) == (
        "master",
        "morning",
        "published",
        None,
    )
    items = _rows(
        db,
        "SELECT t.title FROM plan_items i JOIN tasks t ON t.id = i.task_id"
        " WHERE i.plan_id = %s ORDER BY i.position",
        plan["id"],
    )
    assert [i["title"] for i in items] == MONDAY_PICKS
    [run] = _rows(db, "SELECT status, kind FROM runs WHERE id = %s", plan["master_run_id"])
    assert run == {"status": "succeeded", "kind": "plan"}
    kinds = _rows(
        db,
        "SELECT kind FROM run_events WHERE run_id = %s ORDER BY created_at",
        plan["master_run_id"],
    )
    assert [k["kind"] for k in kinds] == ["dispatched", "result"]

    again = await client.post("/v1/test/tick/planner-tick")
    assert again.status_code == 200, again.text
    assert again.json() == {"woken": 0}
    assert len(_rows(db, "SELECT id FROM daily_plans WHERE day = %s", MONDAY)) == 1


@pytest.mark.req("A1.1", "A1.6")
async def test_enrichment_plays_the_project_agents_recording(
    client: httpx.AsyncClient, db: DbUrls, dbos: type[DBOS], script_store: None
) -> None:
    """T-SEED-19
    A1.1's arrangement: `acme-site` scripted for `enrich` with `enrich__hybrid_invoice`
    and a 300 ms delay. Enriching the hybrid `Send logo drafts to Acme` (no acceptance
    criteria yet) dispatches to the fake, which answers for this task (the recording's
    sentinel task id) after the delay: the task gets the recording's criteria and its
    enrichment ends `done`, and the enrich run ended succeeded."""
    from tumnis.modules.agents.workflows import enrich_task  # noqa: PLC0415

    await _reset(client)
    await _script(client, "acme-site", "enrich", "enrich__hybrid_invoice", delay_ms=300)
    ctx = _ctx(db)
    [task] = _rows(db, "SELECT id FROM tasks WHERE title = 'Send logo drafts to Acme'")

    handle = await dbos.start_workflow_async(enrich_task, str(ctx.workspace_id), str(task["id"]))
    await asyncio.wait_for(handle.get_result(), SETTLE_S)

    [row] = _rows(
        db, "SELECT acceptance_criteria, enrichment_status FROM tasks WHERE id = %s", task["id"]
    )
    assert row["enrichment_status"] == "done"
    assert "The March invoice is sent to Acme" in (row["acceptance_criteria"] or "")
    runs = _rows(db, "SELECT status, kind FROM runs WHERE task_id = %s", task["id"])
    assert runs == [{"status": "succeeded", "kind": "enrich"}]


@pytest.mark.req("A1.2")
async def test_unscripted_fake_dispatch_is_recorded_and_left_running(
    client: httpx.AsyncClient, db: DbUrls, dbos: type[DBOS], script_store: None
) -> None:
    """T-SEED-20
    With no script for the master's `plan`, the fake takes the dispatch (the run is
    written `running` with its `dispatched` event, as a daemon's would be) and answers
    nothing, like a runner that never replies: the tick gives up waiting at its bound and
    nothing is published yet."""
    from tumnis.modules.planning import testing as planning_testing  # noqa: PLC0415

    del dbos
    await _reset(client)
    clock = await client.post("/v1/test/clock", json={"time": MONDAY_PLAN_TIME})
    assert clock.status_code == 200, clock.text
    bound = planning_testing.TICK_WAIT_S
    planning_testing.TICK_WAIT_S = 2.0
    try:
        tick = await client.post("/v1/test/tick/planner-tick")
    finally:
        planning_testing.TICK_WAIT_S = bound
    assert tick.status_code == 200, tick.text
    assert tick.json() == {"woken": 1}
    runs = await _until(lambda: _rows(db, "SELECT id, status, kind FROM runs"))
    assert [(r["status"], r["kind"]) for r in runs] == [("running", "plan")]
    kinds = _rows(db, "SELECT kind FROM run_events WHERE run_id = %s", runs[0]["id"])
    assert [k["kind"] for k in kinds] == ["dispatched"]
    assert _rows(db, "SELECT id FROM daily_plans") == []


@pytest.mark.req("A1.2", "A2.6")
async def test_a_silent_fake_run_ends_when_a_reset_removes_it(
    client: httpx.AsyncClient, db: DbUrls, dbos: type[DBOS], script_store: None
) -> None:
    """T-SEED-23
    A morning build left waiting on an unscripted (silent) fake master would hold the
    one-at-a-time maintenance queue for the whole run timeout, into the next test. Once a
    reset has removed its run, the fake tells the waiting run its runner is lost, so the
    build ends within seconds and the next test's planner tick builds its plan at once
    instead of queueing behind it."""
    from tumnis.modules.planning import api as planning  # noqa: PLC0415
    from tumnis.modules.planning import testing as planning_testing  # noqa: PLC0415

    await _reset(client)
    clock = await client.post("/v1/test/clock", json={"time": MONDAY_PLAN_TIME})
    assert clock.status_code == 200, clock.text
    stale = planning.plan_workflow_id(_ctx(db).workspace_id, MONDAY, "morning")
    bound = planning_testing.TICK_WAIT_S
    planning_testing.TICK_WAIT_S = 1.0
    try:
        tick = await client.post("/v1/test/tick/planner-tick")
    finally:
        planning_testing.TICK_WAIT_S = bound
    assert tick.json() == {"woken": 1}
    assert await _until(lambda: _rows(db, "SELECT id FROM runs WHERE status = 'running'"))

    await _reset(client)

    async def ended() -> bool:
        [status] = await dbos.list_workflows_async(workflow_ids=[stale], load_input=False)
        return status.status not in {"PENDING", "ENQUEUED"}

    for _ in range(int(SILENT_END_S / 0.2)):
        if await ended():
            break
        await asyncio.sleep(0.2)
    assert await ended()

    await _script(client, "tumnis-master", "plan", "plan__monday_four_picks")
    clock = await client.post("/v1/test/clock", json={"time": MONDAY_PLAN_TIME})
    assert clock.status_code == 200, clock.text
    tick = await client.post("/v1/test/tick/planner-tick")
    assert tick.status_code == 200, tick.text
    assert tick.json() == {"woken": 1}
    plans = _rows(db, "SELECT source, status FROM daily_plans WHERE day = %s", MONDAY)
    assert plans == [{"source": "master", "status": "published"}]
