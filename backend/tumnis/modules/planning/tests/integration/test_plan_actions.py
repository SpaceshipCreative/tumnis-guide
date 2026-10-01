"""What the person does with the published plan (P1-11, J1, J6): accept, swap and remove
items, and take a fit offer's split or move. Every item action emits `human.decided`
(R-07) with `item_kind = "plan_item"`; a split or move also decides the issue's
`plan_issue` review item.

Day: Monday 2026-03-09 in New York (the `workspace` fixture); the morning plan is the
due-date fallback unless a test scripts the master.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.planning.tests.integration._plan import (
    MASTER,
    MONDAY,
    PLAN_TIME,
    SKILL,
    TUESDAY,
    build,
    get_task,
    master_on,
    new_project,
    new_task,
    outbox,
    plan_items,
    plan_reply,
    rows,
    set_monday_hours,
    user_ctx,
)

if TYPE_CHECKING:
    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

DAY = MONDAY.isoformat()


def _decided(db: DbUrls, decision: str) -> list[dict[str, Any]]:
    return [
        e["payload"]
        for e in outbox(db, "human.decided")
        if e["payload"]["item_kind"] == "plan_item" and e["payload"]["decision"] == decision
    ]


def _window() -> tuple[datetime, datetime]:
    return datetime(2026, 3, 9, 13, tzinfo=UTC), datetime(2026, 3, 9, 22, tzinfo=UTC)


@pytest.mark.req("J1")
@pytest.mark.wp("P1-11")
async def test_accept_swap_remove(
    dbos: Any,
    workspace: WorkspaceHandle,
    session_client: SessionClient,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-11-17
    Accept moves the task to Today, stamps the item and emits `human.decided`; swap puts the
    first alternate at the same position with a block inside free time that overlaps no other
    block; remove hides the item and leaves the task in Backlog. Each emits `human.decided`.
    """
    project = await new_project(workspace, clock, "Acme site")
    specs = [
        ("Invoice Acme", "human", 60, date(2026, 3, 9)),
        ("Book the venue", "human", 30, date(2026, 3, 10)),
        ("Generate the report", "ai", None, date(2026, 3, 11)),
        ("Review the contract", "human", 45, date(2026, 3, 12)),
        ("Edit the intro video", "hybrid", 30, date(2026, 3, 13)),
        ("Tidy the drive", "human", 30, None),
    ]
    made = [
        await new_task(
            workspace, clock, project, title, label=label, estimate_minutes=est, due_on=due
        )
        for title, label, est, due in specs
    ]
    plan_id = await build(workspace.id, MONDAY, "morning", PLAN_TIME)
    items = {i["task_id"]: i for i in plan_items(db, plan_id)}
    assert set(items) == {t.id for t in made[:5]}
    first, third, fourth, alternate = made[0], made[2], made[3], made[5]

    accepted = await session_client.post(f"/v1/plan/{DAY}/items/{first.id}/accept", json={})
    assert accepted.status_code == 200
    assert (await get_task(workspace, first.id)).status == "today"
    [row] = rows(db, "SELECT accepted_at FROM plan_items WHERE id = %s", items[first.id]["id"])
    assert row["accepted_at"] is not None
    [decision] = _decided(db, "accept")
    assert decision["item_id"] == str(items[first.id]["id"])
    assert (decision["target_type"], decision["target_id"]) == ("task", str(first.id))

    offered = await session_client.get(f"/v1/plan/{DAY}/alternates")
    assert offered.status_code == 200
    assert offered.json()[0]["id"] == str(alternate.id)
    swapped = await session_client.post(
        f"/v1/plan/{DAY}/items/{third.id}/swap", json={"with_task_id": str(alternate.id)}
    )
    assert swapped.status_code == 200
    live = [i for i in plan_items(db, plan_id) if i["removed_at"] is None]
    [new] = [i for i in live if i["task_id"] == alternate.id]
    assert new["position"] == items[third.id]["position"]
    assert new["swapped_from_task_id"] == third.id
    start, end = _window()
    assert start <= new["block_start"] < new["block_end"] <= end
    others = [i for i in live if i["block_start"] is not None and i["id"] != new["id"]]
    assert all(
        new["block_end"] <= o["block_start"] or o["block_end"] <= new["block_start"] for o in others
    )
    [old] = rows(db, "SELECT removed_at FROM plan_items WHERE id = %s", items[third.id]["id"])
    assert old["removed_at"] is not None
    assert len(_decided(db, "swap")) == 1

    removed = await session_client.post(f"/v1/plan/{DAY}/items/{fourth.id}/remove", json={})
    assert removed.status_code == 200
    [gone] = rows(db, "SELECT removed_at FROM plan_items WHERE id = %s", items[fourth.id]["id"])
    assert gone["removed_at"] is not None
    assert (await get_task(workspace, fourth.id)).status == "backlog"
    assert len(_decided(db, "remove")) == 1
    shown = (await session_client.get(f"/v1/plan/{DAY}")).json()
    hidden = next(i for i in shown["items"] if i["task_id"] == str(fourth.id))
    assert hidden["removed_at"] is not None


@pytest.mark.req("J6")
@pytest.mark.wp("P1-11")
async def test_split_creates_subtasks_and_move_pins(  # noqa: PLR0917
    dbos: Any,
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    session_client: SessionClient,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-11-18
    Monday's only gap is 60 minutes and the master picks two 90-minute tasks first. Split
    on the first issue creates Human subtasks of 60 and 30 minutes under the task, each with
    its first action, and resolves the issue and its review item; Move on the second creates
    a `plan_pins` row for Tuesday, resolves that issue, and the task heads Tuesday's
    candidates.
    """
    from tumnis.modules.planning import api as planning  # noqa: PLC0415

    project = await new_project(workspace, clock, "Acme site")
    proposal = await new_task(
        workspace,
        clock,
        project,
        "Write Acme proposal",
        label="human",
        estimate_minutes=90,
        first_action="Open the proposal template",
    )
    lesson = await new_task(
        workspace, clock, project, "Record lesson one", label="human", estimate_minutes=90
    )
    guest = await new_task(
        workspace, clock, project, "Book a guest", label="human", estimate_minutes=30
    )
    await set_monday_hours(workspace, clock, "09:00", "10:00")
    runner = master_on(fake_runner)
    runner.script(
        MASTER,
        SKILL,
        plan_reply(
            (proposal.id, "Due today for Acme"),
            (lesson.id, "Rolled over twice"),
            (guest.id, "Quick one"),
        ),
    )
    plan_id = await build(workspace.id, MONDAY, "morning", PLAN_TIME)
    issues = {
        i["task_id"]: i for i in rows(db, "SELECT * FROM plan_issues WHERE plan_id = %s", plan_id)
    }
    assert set(issues) == {proposal.id, lesson.id}

    split = await session_client.post(
        f"/v1/plan/{DAY}/issues/{issues[proposal.id]['id']}/split", json={}
    )
    assert split.status_code == 200
    subtasks = rows(
        db,
        "SELECT label, estimate_minutes, first_action FROM tasks WHERE parent_id = %s "
        "AND deleted_at IS NULL ORDER BY estimate_minutes",
        proposal.id,
    )
    assert [(s["label"], s["estimate_minutes"]) for s in subtasks] == [("human", 30), ("human", 60)]
    assert all(s["first_action"] == "Open the proposal template" for s in subtasks)
    [resolved] = rows(
        db, "SELECT resolved_at FROM plan_issues WHERE id = %s", issues[proposal.id]["id"]
    )
    assert resolved["resolved_at"] is not None
    [review] = rows(
        db,
        "SELECT decided_at FROM review_items WHERE id = %s",
        issues[proposal.id]["review_item_id"],
    )
    assert review["decided_at"] is not None

    move = await session_client.post(
        f"/v1/plan/{DAY}/issues/{issues[lesson.id]['id']}/move", json={}
    )
    assert move.status_code == 200
    [pin] = rows(db, "SELECT task_id, day FROM plan_pins")
    assert (pin["task_id"], pin["day"]) == (lesson.id, TUESDAY)
    [moved] = rows(db, "SELECT resolved_at FROM plan_issues WHERE id = %s", issues[lesson.id]["id"])
    assert moved["resolved_at"] is not None
    candidates = await planning.plan_candidates(user_ctx(workspace), TUESDAY)
    assert candidates[0] == lesson.id
