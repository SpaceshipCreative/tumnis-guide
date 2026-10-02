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
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest

if TYPE_CHECKING:
    import httpx
    from dbos import DBOS, DBOSClient

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
        _rows(
            db,
            "UPDATE runners SET last_heartbeat_at = %s RETURNING id",
            NOW - timedelta(hours=1),
        )
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


NO_SCRIPT_S = 5.0  # "at once": far under the 90 s run timeout a silent fake would hold


@pytest.mark.req("A1.2")
async def test_unscripted_fake_dispatch_fails_at_once(
    client: httpx.AsyncClient, db: DbUrls, dbos: type[DBOS], script_store: None
) -> None:
    """T-SEED-20 (rewritten by Scott decision 74)
    With no script for the master's `plan`, the fake takes the dispatch (the run is
    written with its `dispatched` event, as a daemon's would be) and answers at once with
    a 'no script' failure, instead of staying silent for the run timeout: the run ends
    `failed` with a `no_script` error and a `result` event, and the one planner tick
    publishes the due-date fallback plan within seconds."""
    del dbos
    await _reset(client)
    clock = await client.post("/v1/test/clock", json={"time": MONDAY_PLAN_TIME})
    assert clock.status_code == 200, clock.text
    started = time.monotonic()
    tick = await client.post("/v1/test/tick/planner-tick")
    assert tick.status_code == 200, tick.text
    assert tick.json() == {"woken": 1}
    plans = await _until(
        lambda: _rows(db, "SELECT source, master_run_id FROM daily_plans WHERE day = %s", MONDAY),
        NO_SCRIPT_S,
    )
    assert time.monotonic() - started < NO_SCRIPT_S
    runs = _rows(db, "SELECT id, status, kind, error FROM runs")
    assert [(r["status"], r["kind"]) for r in runs] == [("failed", "plan")]
    assert str(runs[0]["error"]).startswith("no_script")
    kinds = _rows(
        db, "SELECT kind FROM run_events WHERE run_id = %s ORDER BY created_at", runs[0]["id"]
    )
    assert [k["kind"] for k in kinds] == ["dispatched", "result"]
    assert plans == [{"source": "fallback", "master_run_id": runs[0]["id"]}]


SLOW_SCRIPT_MS = 60_000  # far past SILENT_END_S: the fake is still waiting when the reset comes


@pytest.mark.req("A1.2", "A2.6")
async def test_a_silent_fake_run_ends_when_a_reset_removes_it(
    client: httpx.AsyncClient, db: DbUrls, dbos: type[DBOS], script_store: None
) -> None:
    """T-SEED-23 (setup moved to a slow script by coordinator decision 79, after Scott
    decision 74 made an unscripted fake fail at once)
    A morning build left waiting on a fake master whose scripted answer is still far off
    would hold the one-at-a-time maintenance queue until the answer, into the next test.
    Once a reset has removed its run, the fake tells the waiting run its runner is lost, so
    the build ends within seconds and the next test's planner tick builds its plan at once
    instead of queueing behind it."""
    from tumnis.modules.planning import api as planning  # noqa: PLC0415
    from tumnis.modules.planning import testing as planning_testing  # noqa: PLC0415

    await _reset(client)
    await _script(
        client, "tumnis-master", "plan", "plan__monday_four_picks", delay_ms=SLOW_SCRIPT_MS
    )
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


@pytest.mark.req("A1.2", "A2.6")
async def test_a_late_scripted_answer_ends_a_run_a_reset_removed(
    client: httpx.AsyncClient, db: DbUrls, dbos: type[DBOS], script_store: None
) -> None:
    """T-SEED-24
    A scripted answer that comes after a reset has removed its run (the tick gave up
    waiting, the test moved on) still ends the waiting run: the fake tells it its runner
    is lost, so the morning build ends within seconds of the answer instead of holding
    the maintenance queue for the rest of the run timeout."""
    from tumnis.modules.planning import api as planning  # noqa: PLC0415
    from tumnis.modules.planning import testing as planning_testing  # noqa: PLC0415

    await _reset(client)
    await _script(client, "tumnis-master", "plan", "plan__monday_four_picks", delay_ms=3000)
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


OLD_WORLD_MIN = 9  # more than the events queue runs at once (worker_concurrency 8)
CANCEL_SEEN_S = 2.0


@pytest.mark.req("A2.1", "A2.2")
async def test_a_reset_cancels_the_event_deliveries_of_the_world_it_removed(
    client: httpx.AsyncClient, db: DbUrls, dbos: type[DBOS], script_store: None
) -> None:
    """T-SEED-29
    A reset empties every table but not the DBOS events queue. Deliveries queued for the
    world it removed then fail on missing rows and back off, holding the queue's slots,
    and the next test's events wait behind them: after a few resets `run.requested`
    waited there for over a minute, so A2.1 and A2.2 never saw their run start. A reset
    cancels every event delivery still queued or running from before it, and the new
    world's own deliveries (its seed's events) start without waiting behind them."""
    from dbos import SetWorkflowID  # noqa: PLC0415

    from tumnis.core.events import (  # noqa: PLC0415
        EVENTS_QUEUE,
        EventEnvelope,
        deliver_event,
        delivery_id,
        relay_once,
        subscribers_for,
    )

    await _reset(client)
    removed = [EventEnvelope.from_outbox_row(row) for row in _rows(db, "SELECT * FROM outbox")]
    await _reset(client)
    # The removed world's events, delivered now: their rows are gone, so they fail and
    # back off, as a delivery still queued when a reset ran does.
    old: list[str] = []
    for envelope in removed:
        fresh = envelope.model_copy(update={"event_id": uuid4()})
        for sub in subscribers_for(fresh.name):
            if sub.direct:
                continue
            wf_id = delivery_id(fresh.event_id, sub.name)
            with SetWorkflowID(wf_id):
                await dbos.enqueue_workflow_async(
                    EVENTS_QUEUE, deliver_event, sub.name, fresh.model_dump(mode="json")
                )
            old.append(wf_id)

    async def waiting(ids: list[str], statuses: set[str]) -> list[str]:
        found = await dbos.list_workflows_async(workflow_ids=ids, load_input=False)
        return [w.workflow_id for w in found if w.status in statuses]

    assert len(await waiting(old, {"PENDING", "ENQUEUED"})) >= OLD_WORLD_MIN

    await _reset(client)

    cancelled = await _until_async(
        lambda: waiting(old, {"PENDING", "ENQUEUED"}), CANCEL_SEEN_S, want=[]
    )
    assert cancelled == []
    await relay_once(1000)
    new = [
        delivery_id(row["event_id"], sub.name)
        for row in _rows(db, "SELECT event_id, name FROM outbox")
        for sub in subscribers_for(row["name"])
        if not sub.direct
    ]
    assert len(await dbos.list_workflows_async(workflow_ids=new, load_input=False)) > 0
    assert await _until_async(lambda: waiting(new, {"ENQUEUED"}), SETTLE_S, want=[]) == []


UNSERVED_QUEUE = "app16-unserved"  # no worker dequeues it: what is put there stays queued
OUTSTANDING = {"ENQUEUED", "PENDING", "DELAYED"}


@pytest.mark.req("A2.1", "A2.2", "A2.6")
async def test_a_reset_ends_every_workflow_of_the_world_it_removed(
    client: httpx.AsyncClient,
    db: DbUrls,
    dbos: type[DBOS],
    dbos_client: DBOSClient,
    script_store: None,
) -> None:
    """APP-16 (application test): a reset cancelled only queued `deliver_event` workflows.
    Notification deliveries, labelling, run and plan steps and focus workflows of the world
    it removed kept running and wrote into the new one (foreign-key errors on
    `delivery_attempts`, `review_items`, `daily_plans`), and pending focus workflows heard
    later tests' ticks. A reset now ends every workflow still queued, delayed or running
    from before it, whatever its name, and leaves a schedule's own runs alone (their
    schedule keeps firing for the new world)."""
    from dbos import SetWorkflowID  # noqa: PLC0415

    from tumnis.modules.agents.human_flows import question_flow  # noqa: PLC0415

    await _reset(client)
    workspace = str(_ctx(db).workspace_id)

    # Running: a question flow parked on its human, whose row the next reset removes.
    parked = f"app16-question-{uuid4()}"
    with SetWorkflowID(parked):
        await dbos.start_workflow_async(question_flow, workspace, str(uuid4()))
    # Queued behind a busy queue (here one no worker serves), under several names.
    queued: list[str] = []
    for name in (
        "notifications.deliver_notification",
        "decisions.label_task",
        "focus_session",
        "enrich_task",
    ):
        queued.append(f"app16-{uuid4()}")
        await dbos_client.enqueue_async(
            {"workflow_name": name, "queue_name": UNSERVED_QUEUE, "workflow_id": queued[-1]},
            workspace,
            str(uuid4()),
        )
    # Delayed: a notification delivery due in an hour.
    delayed = f"app16-delayed-{uuid4()}"
    await dbos_client.enqueue_async(
        {
            "workflow_name": "notifications.deliver_notification",
            "queue_name": "notifications",
            "workflow_id": delayed,
            "delay_seconds": 3600,
        },
        workspace,
        str(uuid4()),
    )
    # A schedule's own run, queued: a schedule is not the removed world's.
    schedule = f"app16-schedule-{uuid4()}"
    await dbos_client.create_schedule_async(
        schedule_name=schedule,
        workflow_name="planner_tick",
        schedule="0 0 1 1 *",  # once a year: it never fires during the test
        queue_name=UNSERVED_QUEUE,
    )
    scheduled = dbos_client.trigger_schedule(schedule).get_workflow_id()
    removed = [parked, *queued, delayed]

    async def outstanding(ids: list[str]) -> list[str]:
        found = await dbos.list_workflows_async(workflow_ids=ids, load_input=False)
        return sorted(w.workflow_id for w in found if w.status in OUTSTANDING)

    assert await _until_async(lambda: outstanding(removed), SETTLE_S, want=sorted(removed)) == (
        sorted(removed)
    )
    assert await outstanding([scheduled]) == [scheduled]

    await _reset(client)

    assert await _until_async(lambda: outstanding(removed), CANCEL_SEEN_S, want=[]) == []
    # Ended by the reset, not by failing on the removed rows.
    ended = await dbos.list_workflows_async(workflow_ids=removed, load_input=False)
    assert {w.workflow_id: w.status for w in ended} == dict.fromkeys(removed, "CANCELLED")
    assert await outstanding([scheduled]) == [scheduled]
    assert await dbos_client.get_schedule_async(schedule) is not None


async def _until_async(check: Callable[[], Awaitable[Any]], timeout_s: float, *, want: Any) -> Any:
    deadline = time.monotonic() + timeout_s
    while True:
        value = await check()
        if value == want or time.monotonic() >= deadline:
            return value
        await asyncio.sleep(0.1)
