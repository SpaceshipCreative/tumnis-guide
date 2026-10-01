"""Building and publishing the daily plan (P1-11, FR-4.3, FR-1.2, J6): the master picks
and gives reasons, the app places Human and Hybrid tasks into free blocks and validates;
an unreachable master or an invalid reply gives the due-date fallback with a notice; a
pick with no big enough gap becomes an offer; nothing re-plans on its own; a killed
worker publishes once.

Day: Monday 2026-03-09 in New York (the `workspace` fixture), default working hours
09:00 to 18:00 and no calendar events, so the free block is the whole window unless a
test narrows Monday's hours. The master's replies are recorded results
(`tests/fakes/recordings/runner/plan__*.result.json`) naming tasks by title
(`title:<task title>`); the fake runner puts in the id of the candidate with that title.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest

from tumnis.modules.planning.tests.integration._plan import (
    MASTER,
    MONDAY,
    PLAN_TIME,
    SKILL,
    TUESDAY,
    build,
    build_runs,
    builds_settled,
    get_task,
    master_on,
    move_task,
    new_project,
    new_task,
    outbox,
    plan_items,
    plans,
    published,
    recorded,
    relay,
    rows,
    set_monday_hours,
    tick,
    until,
)

if TYPE_CHECKING:
    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkerKillerFactory, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

# title -> (label, estimate); the titles the recorded replies pick.
TASKS: dict[str, tuple[str, int | None]] = {
    "Send logo drafts to Acme": ("hybrid", 30),
    "Invoice Acme for phase one": ("human", 60),
    "Record lesson one": ("human", 90),
    "Generate March analytics report": ("ai", None),
    "Write Acme proposal": ("human", 90),
    "Book a guest for lesson three": ("human", 30),
    "Draft the launch email sequence": ("ai", None),
}


async def _tasks(workspace: WorkspaceHandle, clock: FixedClock) -> dict[str, Any]:
    project = await new_project(workspace, clock, "Acme site")
    made = {}
    for title, (label, estimate) in TASKS.items():
        due = {"due_on": MONDAY} if title == "Write Acme proposal" else {}
        made[title] = await new_task(
            workspace, clock, project, title, label=label, estimate_minutes=estimate, **due
        )
    return made


def _free_monday() -> tuple[datetime, datetime]:
    """Monday's default working window, 09:00 to 18:00 EDT."""
    return (
        datetime(2026, 3, 9, 13, tzinfo=UTC),
        datetime(2026, 3, 9, 22, tzinfo=UTC),
    )


def _picked_titles(name: str) -> list[str]:
    return [pick["task_id"].removeprefix("title:") for pick in recorded(name)["picks"]]


@pytest.mark.req("FR-4.3", "FR-1.2")
@pytest.mark.wp("P1-11")
async def test_master_plan_published_with_blocks_and_reasons(
    dbos: Any,
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-11-09
    The fake master returns 4 picks (one AI): the plan is the master's (`source = "master"`,
    its run and the master's profile version kept), 4 items in pick order with the master's
    reasons, Human and Hybrid blocks inside the free block and as long as their estimates,
    the AI item without a block, and `plan.published` emitted once.
    """
    made = await _tasks(workspace, clock)
    runner = master_on(fake_runner)
    reply = recorded("plan__monday_four_picks")
    runner.script(MASTER, SKILL, reply)

    plan_id = await build(workspace.id, MONDAY, "morning", PLAN_TIME)

    plan = published(db)
    assert plan is not None
    assert plan["id"] == plan_id
    assert plan["source"] == "master"
    assert plan["trigger"] == "morning"
    assert plan["notice"] is None
    assert plan["master_run_id"] is not None
    [run] = rows(db, "SELECT id, kind, status FROM runs WHERE id = %s", plan["master_run_id"])
    assert (run["kind"], run["status"]) == ("plan", "succeeded")
    items = plan_items(db, plan_id)
    titles = _picked_titles("plan__monday_four_picks")
    assert [made_title(made, i["task_id"]) for i in items] == titles
    assert [i["reason"] for i in items] == [p["reason"] for p in reply["picks"]]
    assert [i["position"] for i in items] == [1, 2, 3, 4]
    start, end = _free_monday()
    for item in items:
        label, estimate = TASKS[made_title(made, item["task_id"])]
        if label == "ai":
            assert item["block_start"] is None
            assert item["block_end"] is None
        else:
            assert start <= item["block_start"] < item["block_end"] <= end
            assert item["block_end"] - item["block_start"] == timedelta(minutes=estimate or 0)
    blocks = sorted((i["block_start"], i["block_end"]) for i in items if i["block_start"])
    assert all(a[1] <= b[0] for a, b in pairwise(blocks))
    events = outbox(db, "plan.published")
    assert len(events) == 1
    assert events[0]["payload"]["source"] == "master"
    assert events[0]["payload"]["task_ids"] == [str(i["task_id"]) for i in items]


def made_title(made: dict[str, Any], task_id: UUID) -> str:
    [title] = [title for title, task in made.items() if task.id == task_id]
    return title


@pytest.mark.req("FR-4.3")
@pytest.mark.wp("P1-11")
async def test_master_unreachable_uses_fallback_with_notice(
    dbos: Any,
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-11-10
    The master profile is offline: nothing is dispatched, and the published plan is the
    due-date fallback (`source = "fallback"`, `notice = "agent_offline"`) with the task due
    today first and at most 5 items.
    """
    made = await _tasks(workspace, clock)
    runner = master_on(fake_runner)
    runner.offline(MASTER)

    plan_id = await build(workspace.id, MONDAY, "morning", PLAN_TIME)

    plan = published(db)
    assert plan is not None
    assert plan["id"] == plan_id
    assert (plan["source"], plan["notice"]) == ("fallback", "agent_offline")
    assert plan["master_run_id"] is None
    assert rows(db, "SELECT id FROM runs") == []
    items = plan_items(db, plan_id)
    assert 0 < len(items) <= 5
    assert items[0]["task_id"] == made["Write Acme proposal"].id
    assert all(i["reason"] for i in items)
    assert len(outbox(db, "plan.published")) == 1


INVALID: list[Any] = [
    pytest.param("plan__six_picks", "too_many_items", id="six_picks"),
    pytest.param("plan__unknown_task", "unknown_task", id="unknown_task"),
    pytest.param("plan__no_reason", "missing_reason", id="no_reason"),
    pytest.param("plan__no_json", "no_json", id="no_json"),
]


@pytest.mark.req("FR-4.3")
@pytest.mark.wp("P1-11")
@pytest.mark.parametrize(("script", "code"), INVALID)
async def test_invalid_master_reply_rejected_and_logged(  # noqa: PLR0917
    dbos: Any,
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    script: str,
    code: str,
) -> None:
    """T-P1-11-11
    A reply with 6 picks, an unknown task id, a missing reason, or no JSON at all is
    rejected whole: the plan is the fallback with `notice = "invalid_plan"` and a
    `fallback_reason` naming the violation, and the run row keeps the raw output.
    """
    await _tasks(workspace, clock)
    runner = master_on(fake_runner)
    reply = recorded(script)
    runner.script(MASTER, SKILL, reply)

    await build(workspace.id, MONDAY, "morning", PLAN_TIME)

    plan = published(db)
    assert plan is not None
    assert (plan["source"], plan["notice"]) == ("fallback", "invalid_plan")
    assert code in (plan["fallback_reason"] or "")
    [run] = rows(db, "SELECT id, kind, output FROM runs")
    assert run["kind"] == "plan"
    assert run["id"] == plan["master_run_id"]
    if reply is None:
        assert run["output"] is None
    else:
        assert run["output"]["picks"][0]["reason"] == reply["picks"][0]["reason"]
        assert len(run["output"]["picks"]) == len(reply["picks"])
    assert len(outbox(db, "plan.published")) == 1


@pytest.mark.req("J6")
@pytest.mark.wp("P1-11")
async def test_unplaceable_pick_gets_issue_and_offer(
    dbos: Any,
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-11-12
    Monday's hours are 09:00 to 10:00 (one 60-minute gap) and the master picks the
    90-minute `Write Acme proposal` first: it gets no item, one `plan_issues` row offering
    a split of 60 + 30 or a move to Tuesday, and one `plan_issue` review item; the plan is
    still the master's, and the next pick takes the gap.
    """
    made = await _tasks(workspace, clock)
    await set_monday_hours(workspace, clock, "09:00", "10:00")
    runner = master_on(fake_runner)
    runner.script(MASTER, SKILL, recorded("plan__ninety_first"))

    plan_id = await build(workspace.id, MONDAY, "morning", PLAN_TIME)

    plan = published(db)
    assert plan is not None
    assert plan["source"] == "master"
    proposal = made["Write Acme proposal"].id
    items = plan_items(db, plan_id)
    assert proposal not in [i["task_id"] for i in items]
    booked = next(i for i in items if i["task_id"] == made["Book a guest for lesson three"].id)
    assert booked["block_start"] == datetime(2026, 3, 9, 13, tzinfo=UTC)
    [issue] = rows(db, "SELECT * FROM plan_issues WHERE plan_id = %s", plan_id)
    assert issue["task_id"] == proposal
    assert issue["offer"] == {"split": [60, 30], "move_to": TUESDAY.isoformat()}
    assert issue["resolved_at"] is None
    [item] = rows(
        db,
        "SELECT id, target_id FROM review_items WHERE kind = 'plan_issue' AND decided_at IS NULL",
    )
    assert item["target_id"] == proposal
    assert issue["review_item_id"] == item["id"]


@pytest.mark.req("FR-4.3")
@pytest.mark.wp("P1-11")
async def test_blocked_today_task_stays_until_replan(
    dbos: Any,
    workspace: WorkspaceHandle,
    session_client: SessionClient,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-11-13
    After the morning plan is published, a planned task moves to Waiting on human: no new
    plan row and no `build_plan` enqueued; `GET /v1/plan/{day}` shows it at its position
    with `blocked = true`; after Re-plan it is gone from the new plan.
    """
    await _tasks(workspace, clock)
    plan_id = await build(workspace.id, MONDAY, "morning", PLAN_TIME)
    items = plan_items(db, plan_id)
    first = items[0]
    await move_task(
        workspace, clock, first["task_id"], "in_progress", "waiting_on_human", agent=True
    )
    for _ in range(3):
        await relay()
    assert await until(builds_settled)

    assert len(plans(db)) == 1
    assert len(build_runs()) == 1
    response = await session_client.get(f"/v1/plan/{MONDAY.isoformat()}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(plan_id)
    shown = next(i for i in body["items"] if i["task_id"] == str(first["task_id"]))
    assert shown["position"] == first["position"]
    assert shown["blocked"] is True
    others = [i for i in body["items"] if i["task_id"] != str(first["task_id"])]
    assert all(i["blocked"] is False for i in others)

    replan = await session_client.post("/v1/plan/replan", json={"day": MONDAY.isoformat()})
    assert replan.status_code == 202
    assert await until(lambda: len(plans(db)) == 2)
    assert await until(builds_settled)
    new = published(db)
    assert new is not None
    assert new["trigger"] == "replan"
    assert [p["status"] for p in plans(db)] == ["superseded", "published"]
    assert first["task_id"] not in [i["task_id"] for i in plan_items(db, new["id"])]
    assert (await get_task(workspace, first["task_id"])).status == "waiting_on_human"


@pytest.mark.req("FR-4.3")
@pytest.mark.wp("P1-11")
async def test_replan_on_demand_supersedes_and_respects_now(
    dbos: Any,
    workspace: WorkspaceHandle,
    session_client: SessionClient,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-11-14
    Re-plan at 14:07 local: the morning plan becomes `superseded`, the new one is published
    with `trigger = "replan"`, and every new block starts at or after 14:10.
    """
    await _tasks(workspace, clock)
    morning = await build(workspace.id, MONDAY, "morning", PLAN_TIME)
    clock.set(datetime(2026, 3, 9, 18, 7, tzinfo=UTC))  # 14:07 EDT

    response = await session_client.post("/v1/plan/replan", json={"day": MONDAY.isoformat()})
    assert response.status_code == 202
    assert await until(lambda: len(plans(db)) == 2)
    assert await until(builds_settled)

    [old] = [p for p in plans(db) if p["id"] == morning]
    assert old["status"] == "superseded"
    new = published(db)
    assert new is not None
    assert new["id"] != morning
    assert new["trigger"] == "replan"
    blocks = [i["block_start"] for i in plan_items(db, new["id"]) if i["block_start"]]
    assert blocks
    assert all(start >= datetime(2026, 3, 9, 18, 10, tzinfo=UTC) for start in blocks)


@pytest.mark.req("FR-4.3")
@pytest.mark.wp("P1-11")
async def test_nothing_replans_during_the_day(
    dbos: Any, workspace: WorkspaceHandle, clock: FixedClock, db: DbUrls
) -> None:
    """T-P1-11-15
    Over a simulated Monday of task creates, edits, status changes and calendar syncs, with
    the planner ticking through the rest of the local day, exactly one `build_plan` runs:
    the morning one.
    """
    from tumnis.core.outbox import emit  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.calendar.api import CalendarSyncedV1, SyncWindow  # noqa: PLC0415
    from tumnis.modules.planning.tests.integration._plan import user_ctx  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    made = await _tasks(workspace, clock)
    await tick(PLAN_TIME)
    assert await until(builds_settled)
    assert len(build_runs()) == 1

    project = made["Write Acme proposal"].project_id
    at = PLAN_TIME
    for hour in range(14):
        at = PLAN_TIME + timedelta(hours=hour, minutes=5)
        clock.set(at)
        task = await new_task(workspace, clock, project, f"Afternoon task {hour}", label="human")
        ctx = user_ctx(workspace)
        async with tenant_session(ctx) as s:
            await tasks.update_task(
                s,
                ctx.actor,
                task.id,
                tasks.TaskPatch(title=f"Renamed {hour}", version=task.version),
                task.version,
                now=at,
            )
        if hour % 3 == 0:
            await move_task(workspace, clock, task.id, "today")
        async with tenant_session(ctx) as s:
            await emit(
                s,
                CalendarSyncedV1(
                    connection_id=UUID(int=hour + 1),
                    window=SyncWindow(start=at, end=at + timedelta(days=7)),
                ),
                occurred_at=at,
            )
        await relay()
        await tick(at.replace(minute=(at.minute // 5) * 5))
    assert at.astimezone(UTC) < datetime(2026, 3, 10, 4, tzinfo=UTC)  # still Monday in New York
    assert await until(builds_settled)

    assert len(build_runs()) == 1
    assert len(plans(db)) == 1
    assert published(db) is not None


@pytest.mark.req("FR-4.3")
@pytest.mark.wp("P1-11")
async def test_killed_worker_publishes_once(
    worker_killer: WorkerKillerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-11-16
    A worker killed at `planning.publish_step`, right after the plan committed: the
    restarted worker finishes `build_plan`, and there is one published plan and one
    `plan.published`.
    """
    from tests.fixtures import KILLED_EXIT  # noqa: PLC0415

    await _tasks(workspace, clock)
    killer = worker_killer("planning.publish_step", events=0)
    workflow_id = f"build_plan:kill-test:{workspace.id}"

    code = await killer.enqueue_until_killed(
        queue_name="maintenance",
        workflow_name="build_plan",
        workflow_id=workflow_id,
        args=(workspace.id, MONDAY, "morning", PLAN_TIME),
    )
    assert code == KILLED_EXIT, killer.log_tail()
    assert await killer.restart_until_done(workflow_id) == "SUCCESS", killer.log_tail()

    assert len(plans(db)) == 1
    assert published(db) is not None
    assert len(outbox(db, "plan.published")) == 1
