"""tasks pure rules: no I/O, `now` passed in (P0-18, FR-3.1 to FR-3.8, FR-4.4).

- The state machine (FR-3.2, ARCHITECTURE's task diagram): `TRANSITIONS` names every edge
  with the actors that may take it and the labels it applies to; any other move, from = to
  included, is `TransitionNotAllowed` (409 `transition_not_allowed`). Agents only plan,
  start, ask and post results; only a human finishes work (FR-5.8), and only a Human-labelled
  task goes from In progress straight to Done (there is no Today -> Done shortcut: a quick
  Human task needs Start, then Done).
- `apply_transition` adds the side effects: the first start sets `started_at`; Done sets
  `completed_at` and, for Human and Hybrid work that started, `actual_minutes` (wall clock,
  rounded up); the system's rollover of a Today task counts in `rollover_count`; reopening a
  done task clears the completion fields.
- Estimates (FR-4.4): minutes of human time, required from agents for Human and Hybrid
  work, never kept on AI-only work, never required while the label is pending (R-08).
- Layout (FR-3.4, FR-3.8): a Human or Hybrid subtask at or above the card threshold is its
  own card; below it, unestimated or AI, it is a checklist item on its parent. Computed on
  every read, never stored.
- Today's order: priority, then due date (undated last), then age.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from math import ceil
from typing import Any, Final, Literal
from uuid import UUID


class Status(StrEnum):
    BACKLOG = "backlog"
    TODAY = "today"
    IN_PROGRESS = "in_progress"
    WAITING_ON_HUMAN = "waiting_on_human"
    IN_REVIEW = "in_review"
    DONE = "done"


class Label(StrEnum):
    HUMAN = "human"
    AI = "ai"
    HYBRID = "hybrid"


# tasks.label is nullable: None means pending (R-08). Rules take `Label | None`.

LabelSource = Literal["user", "jev", "agent", "fallback"]  # R-08; `fallback` is vLLM (P1-02)
LabelState = Literal["pending", "suggested", "confirmed"]


def may_auto_label(label_source: LabelSource | None) -> bool:
    """False once the user has chosen a label (source `user`); True otherwise (P1-07)."""
    return label_source != "user"


def label_state(label: Label | None, suggestion: Label | None) -> LabelState:
    """The label chip's state (P1-07): confirmed when the label is set, suggested when only
    a low-confidence suggestion exists, pending otherwise."""
    if label is not None:
        return "confirmed"
    return "pending" if suggestion is None else "suggested"


class ActorKind(StrEnum):
    HUMAN = "human"  # session user
    AGENT = "agent"  # API key or task token (any tool, not only Hermes)
    SYSTEM = "system"  # housekeeping, planner, resume after a human decision


class Trigger(StrEnum):
    PLAN = "plan"
    ROLLOVER = "rollover"
    START = "start"
    ASK = "ask_human"
    ANSWERED = "answered"
    RESULT = "result"
    REJECT = "reject"
    ACCEPT = "accept"
    MARK_DONE = "mark_done"
    RESET = "reset"


EVERY_LABEL: Final[frozenset[Label | None]] = frozenset([*Label, None])


@dataclass(frozen=True, slots=True)
class Edge:
    trigger: Trigger
    actors: frozenset[ActorKind]
    labels: frozenset[Label | None] = EVERY_LABEL  # labels the edge applies to; None = pending


H, A, S = ActorKind.HUMAN, ActorKind.AGENT, ActorKind.SYSTEM
B, T, P, W, R, D = (
    Status.BACKLOG,
    Status.TODAY,
    Status.IN_PROGRESS,
    Status.WAITING_ON_HUMAN,
    Status.IN_REVIEW,
    Status.DONE,
)

TRANSITIONS: Final[Mapping[tuple[Status, Status], Edge]] = {
    (B, T): Edge(Trigger.PLAN, frozenset({H, A, S})),  # A: the master plans (P1-11)
    (B, P): Edge(Trigger.START, frozenset({H, A})),  # "start straight from Backlog"
    (T, P): Edge(Trigger.START, frozenset({H, A})),
    (T, B): Edge(Trigger.ROLLOVER, frozenset({H, S})),  # H here is a reset, see apply_transition
    (P, W): Edge(Trigger.ASK, frozenset({A})),  # ask_human or request_approval
    (W, P): Edge(Trigger.ANSWERED, frozenset({H, S})),  # S: agents resume on human.decided
    (P, R): Edge(Trigger.RESULT, frozenset({A})),
    (R, P): Edge(Trigger.REJECT, frozenset({H})),
    (R, D): Edge(Trigger.ACCEPT, frozenset({H})),  # FR-5.8: never auto-completed
    (P, D): Edge(Trigger.MARK_DONE, frozenset({H}), frozenset({Label.HUMAN})),
    # "Any status can move to Backlog by a human"
    (P, B): Edge(Trigger.RESET, frozenset({H})),
    (W, B): Edge(Trigger.RESET, frozenset({H})),
    (R, B): Edge(Trigger.RESET, frozenset({H})),
    (D, B): Edge(Trigger.RESET, frozenset({H})),  # reopen
}

# The edges an agent may take, derived once (T-P0-18-02 pins them).
AGENT_EDGES: Final = frozenset(pair for pair, edge in TRANSITIONS.items() if A in edge.actors)


class TransitionNotAllowed(Exception):  # noqa: N818  # the plan's name
    code = "transition_not_allowed"

    def __init__(self, frm: Status, to: Status, actor: ActorKind, label: Label | None) -> None:
        super().__init__(f"{actor} cannot move a {label or 'pending'} task from {frm} to {to}")
        self.frm, self.to, self.actor, self.label = frm, to, actor, label


def check_transition(frm: Status, to: Status, actor: ActorKind, label: Label | None) -> Edge:
    """The edge from `frm` to `to` when `actor` may take it for a task of `label`;
    TransitionNotAllowed otherwise (from = to included)."""
    edge = TRANSITIONS.get((frm, to))
    if edge is None or actor not in edge.actors or label not in edge.labels:
        raise TransitionNotAllowed(frm, to, actor, label)
    return edge


@dataclass(frozen=True, slots=True)
class TaskState:
    status: Status
    label: Label | None
    rollover_count: int
    started_at: datetime | None
    completed_at: datetime | None
    actual_minutes: int | None


HUMAN_TIME: Final = frozenset({Label.HUMAN, Label.HYBRID})  # labels whose minutes count


def actual_minutes(started_at: datetime, completed_at: datetime) -> int:
    """Whole wall-clock minutes from start to completion, rounded up (never negative)."""
    seconds = (completed_at - started_at).total_seconds()
    return max(0, ceil(seconds / 60))


def apply_transition(s: TaskState, to: Status, actor: ActorKind, now: datetime) -> TaskState:
    """The task after moving to `to` at `now`, side effects applied (see the module doc);
    TransitionNotAllowed when the matrix has no such edge for this actor and label."""
    edge = check_transition(s.status, to, actor, s.label)
    after = replace(s, status=to)
    if to is Status.IN_PROGRESS and after.started_at is None:
        after = replace(after, started_at=now)
    if to is Status.DONE:
        minutes = None
        if s.label in HUMAN_TIME and after.started_at is not None:
            minutes = actual_minutes(after.started_at, now)
        after = replace(after, completed_at=now, actual_minutes=minutes)
    elif edge.trigger is Trigger.ROLLOVER and actor is ActorKind.SYSTEM:
        after = replace(after, rollover_count=s.rollover_count + 1)
    elif s.status is Status.DONE:  # reopened: the completion no longer holds
        after = replace(after, completed_at=None, actual_minutes=None)
    return after


# --- Estimates (FR-4.4) -----------------------------------------------------------------------


class EstimateRequired(Exception):  # noqa: N818  # the plan's name
    code = "estimate_required"

    def __init__(self, label: Label) -> None:
        super().__init__(f"A {label} task needs an estimate in minutes of human time")
        self.label = label


def normalize_estimate(label: Label | None, estimate: int | None, actor: ActorKind) -> int | None:
    """The estimate to store. AI-only work carries none (dropped whoever gives it); a
    pending label keeps what was given and never needs one; Human and Hybrid work needs
    one from an agent (EstimateRequired), while a human may skip it in phase 0 (the
    project agent fills it in, P1-08)."""
    if label is Label.AI:
        return None
    if label is not None and estimate is None and actor is ActorKind.AGENT:
        raise EstimateRequired(label)
    return estimate


# --- Layout (FR-3.4, FR-3.8) -------------------------------------------------------------------


class Placement(StrEnum):
    CARD = "card"
    NESTED = "nested"


def subtask_placement(
    label: Label | None, estimate_minutes: int | None, threshold_min: int
) -> Placement:
    """Where a subtask shows: its own card at or above the threshold (Human, Hybrid and a
    pending label, laid out like Human), else nested on the parent. AI subtasks and
    unestimated ones nest (plan default)."""
    if label is Label.AI or estimate_minutes is None or estimate_minutes < threshold_min:
        return Placement.NESTED
    return Placement.CARD


@dataclass(frozen=True, slots=True)
class BoardTask:
    """What the layout needs from one task."""

    id: UUID
    parent_id: UUID | None
    status: Status
    column_id: UUID | None
    board_rank: str
    label: Label | None
    estimate_minutes: int | None


@dataclass(frozen=True, slots=True)
class ColumnDef:
    id: UUID
    status: Status  # the one status the column holds (phase 0)


@dataclass(frozen=True, slots=True)
class CardLayout:
    task_id: UUID
    checklist: tuple[UUID, ...]  # nested subtasks, in board order


@dataclass(frozen=True, slots=True)
class ColumnLayout:
    column_id: UUID
    cards: tuple[CardLayout, ...]


@dataclass(frozen=True, slots=True)
class BoardLayout:
    columns: tuple[ColumnLayout, ...]


def _order(task: BoardTask) -> tuple[str, UUID]:
    return (task.board_rank, task.id)


def layout_board(
    tasks: Sequence[BoardTask], columns: Sequence[ColumnDef], threshold_min: int
) -> BoardLayout:
    """Cards per column (in `columns` order), each with its checklist. A task sits in its
    own column when that column holds its status, else in the first column for its status;
    a subtask nests on its parent when `subtask_placement` says so and the parent is on the
    board. A task whose status has no column is left off (PUT columns never allows that)."""
    by_id = {task.id: task for task in tasks}
    first_for: dict[Status, UUID] = {}
    for column in columns:
        first_for.setdefault(column.status, column.id)
    status_of = {column.id: column.status for column in columns}

    nested: dict[UUID, list[BoardTask]] = {}
    cards: dict[UUID, list[BoardTask]] = {column.id: [] for column in columns}
    for task in sorted(tasks, key=_order):
        parent = by_id.get(task.parent_id) if task.parent_id is not None else None
        if (
            parent is not None
            and subtask_placement(task.label, task.estimate_minutes, threshold_min)
            is Placement.NESTED
        ):
            nested.setdefault(parent.id, []).append(task)
            continue
        own = task.column_id
        column_id = own if own is not None and status_of.get(own) is task.status else None
        column_id = column_id or first_for.get(task.status)
        if column_id is not None:
            cards[column_id].append(task)
    return BoardLayout(
        columns=tuple(
            ColumnLayout(
                column_id=column.id,
                cards=tuple(
                    CardLayout(task.id, tuple(item.id for item in nested.get(task.id, ())))
                    for task in cards[column.id]
                ),
            )
            for column in columns
        )
    )


# --- Today (FR-3.1) -----------------------------------------------------------------------------

PRIORITY_RANK: Final = {"urgent": 0, "high": 1, "normal": 2, "low": 3}


@dataclass(frozen=True, slots=True)
class TodayTask:
    id: UUID
    priority: str  # low | normal | high | urgent
    due_on: date | None
    created_at: datetime


def today_order(tasks: Sequence[TodayTask]) -> list[TodayTask]:
    """Urgent first; then the earliest due date (undated last); then the oldest; then id."""
    return sorted(
        tasks,
        key=lambda t: (
            PRIORITY_RANK.get(t.priority, len(PRIORITY_RANK)),
            t.due_on is None,
            t.due_on or date.max,
            t.created_at,
            t.id,
        ),
    )


# --- Undo (R-09, UX 9) ---------------------------------------------------------------------------

UNDO_FIELDS: Final = frozenset(
    {
        "status",
        "column_id",
        "board_rank",
        "title",
        "label",
        "priority",
        "due_on",
        "estimate_minutes",
        "first_action",
        "acceptance_criteria",
        "parent_id",
        "deleted",
    }
)
# Never undone: rollover_count, started_at, completed_at, actual_minutes (derived or
# history), so undo cannot make the rollover count fall (P0-18's invariant).
_UUID_FIELDS: Final = frozenset({"column_id", "parent_id"})


def _json(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    return value


def undo_snapshot(row: Mapping[Any, Any]) -> dict[str, object]:
    """A task row's undoable fields as JSON values; `deleted` says whether it is in the
    trash (`deleted_at` set)."""
    return {
        field: row["deleted_at"] is not None if field == "deleted" else _json(row[field])
        for field in UNDO_FIELDS
    }


def change_between(
    before: Mapping[Any, Any], after: Mapping[Any, Any]
) -> tuple[dict[str, object], dict[str, object]]:
    """The undoable fields a write changed: (before, after), each holding only those."""
    old, new = undo_snapshot(before), undo_snapshot(after)
    changed = sorted(field for field in UNDO_FIELDS if old[field] != new[field])
    return {f: old[f] for f in changed}, {f: new[f] for f in changed}


def restore_values(
    before: Mapping[str, Any], completed_at: datetime | None, now: datetime
) -> dict[str, Any]:
    """The row values that put a change's `before` back: ids and dates parsed, `deleted`
    as `deleted_at`, and `completed_at` recomputed when the status comes back (kept for
    Done, set to `now` if missing; cleared for any other status)."""
    values: dict[str, Any] = {}
    for field, value in before.items():
        if field not in UNDO_FIELDS:
            continue
        if field == "deleted":
            values["deleted_at"] = now if value else None
        elif field in _UUID_FIELDS and isinstance(value, str):
            values[field] = UUID(value)
        elif field == "due_on" and isinstance(value, str):
            values[field] = date.fromisoformat(value)
        else:
            values[field] = value
    if "status" in values:
        done = values["status"] == Status.DONE
        values["completed_at"] = (completed_at or now) if done else None
    return values
