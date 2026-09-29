"""Details of the tasks rules the spec tests reach only in aggregate (P0-18): the side
effects of each transition and the board layout."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tumnis.modules.tasks.rules import (
    ActorKind,
    BoardTask,
    ColumnDef,
    Label,
    Status,
    TaskState,
    TransitionNotAllowed,
    actual_minutes,
    apply_transition,
    layout_board,
)

NOW = datetime(2026, 3, 9, 12, tzinfo=UTC)
H, A, S = ActorKind.HUMAN, ActorKind.AGENT, ActorKind.SYSTEM


def _state(status: Status, label: Label | None = Label.HUMAN, **kw: object) -> TaskState:
    fields: dict[str, object] = {
        "rollover_count": 0,
        "started_at": None,
        "completed_at": None,
        "actual_minutes": None,
        **kw,
    }
    return TaskState(status, label, **fields)  # type: ignore[arg-type]


@pytest.mark.req("FR-3.2", "FR-4.4")
@pytest.mark.wp("P0-18")
def test_transition_side_effects() -> None:
    """Start sets started_at once; Done records the rounded-up wall minutes of Human and
    Hybrid work; the system's rollover counts, a human's reset does not; reopening clears
    the completion but keeps the start and the rollover count."""
    started = apply_transition(_state(Status.TODAY), Status.IN_PROGRESS, H, NOW)
    assert started.started_at == NOW
    later = NOW + timedelta(minutes=10)
    back = apply_transition(
        apply_transition(started, Status.WAITING_ON_HUMAN, A, later), Status.IN_PROGRESS, S, later
    )
    assert back.started_at == NOW  # the first start stands

    done_at = NOW + timedelta(minutes=94, seconds=1)
    done = apply_transition(back, Status.DONE, H, done_at)
    assert (done.completed_at, done.actual_minutes) == (done_at, 95)

    hybrid = _state(Status.IN_REVIEW, Label.HYBRID, started_at=NOW)
    assert apply_transition(hybrid, Status.DONE, H, NOW + timedelta(minutes=5)).actual_minutes == 5
    unstarted = apply_transition(_state(Status.IN_REVIEW, None), Status.DONE, H, NOW)
    assert unstarted.actual_minutes is None

    rolled = apply_transition(_state(Status.TODAY, rollover_count=2), Status.BACKLOG, S, NOW)
    assert rolled.rollover_count == 3
    reset = apply_transition(_state(Status.TODAY, rollover_count=2), Status.BACKLOG, H, NOW)
    assert reset.rollover_count == 2

    reopened = apply_transition(
        _state(Status.DONE, rollover_count=1, started_at=NOW, completed_at=NOW, actual_minutes=3),
        Status.BACKLOG,
        H,
        NOW,
    )
    assert (reopened.status, reopened.completed_at, reopened.actual_minutes) == (
        Status.BACKLOG,
        None,
        None,
    )
    assert (reopened.started_at, reopened.rollover_count) == (NOW, 1)

    with pytest.raises(TransitionNotAllowed, match="pending"):
        apply_transition(_state(Status.IN_PROGRESS, None), Status.DONE, H, NOW)
    assert actual_minutes(NOW, NOW - timedelta(minutes=1)) == 0


def _task(
    status: Status,
    rank: str,
    *,
    parent: uuid.UUID | None = None,
    column: uuid.UUID | None = None,
    label: Label | None = Label.HUMAN,
    estimate: int | None = 60,
) -> BoardTask:
    return BoardTask(uuid.uuid4(), parent, status, column, rank, label, estimate)


@pytest.mark.req("FR-3.4", "FR-3.2")
@pytest.mark.wp("P0-18")
def test_layout_places_cards_and_checklists() -> None:
    """Cards go to their own column when it holds their status, else to the first column
    for it; cards and checklist items follow the board rank; a subtask whose parent is not
    on the board is a card; a status without a column shows nowhere."""
    backlog, today, second_today = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    columns = [
        ColumnDef(backlog, Status.BACKLOG),
        ColumnDef(today, Status.TODAY),
        ColumnDef(second_today, Status.TODAY),
    ]
    parent = _task(Status.BACKLOG, "a1")
    first = _task(Status.BACKLOG, "a0", column=today)  # stale column: status moved on
    moved = _task(Status.TODAY, "a0", column=second_today)
    small_b = _task(Status.BACKLOG, "b", parent=parent.id, estimate=10)
    small_a = _task(Status.TODAY, "a", parent=parent.id, label=Label.AI, estimate=None)
    big = _task(Status.TODAY, "c", parent=parent.id, estimate=45)
    orphan = _task(Status.TODAY, "d", parent=uuid.uuid4(), estimate=5)
    done = _task(Status.DONE, "a0")

    layout = layout_board([parent, first, moved, small_b, small_a, big, orphan, done], columns, 30)
    cards = {
        c.column_id: [(card.task_id, card.checklist) for card in c.cards] for c in layout.columns
    }
    assert cards[backlog] == [(first.id, ()), (parent.id, (small_a.id, small_b.id))]
    assert cards[today] == [(big.id, ()), (orphan.id, ())]
    assert cards[second_today] == [(moved.id, ())]
    assert done.id not in {task_id for column in cards.values() for task_id, _ in column}
