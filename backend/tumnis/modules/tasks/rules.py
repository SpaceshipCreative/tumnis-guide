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
- Taint (P2-08, SAF-1, design decision 14): what is made from outside content is tainted.
  A new record's taint is the OR of its sources (`derive_taint`); linking can add taint to
  an existing task and unlinking never clears it (`raise_only`); no tainted task may run
  unattended (`may_run_unattended`, which P4-04's scheduler calls).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from math import ceil
from typing import Any, Final, Literal, Protocol
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


# --- The stuck run's step (P4-02, FR-10.5) -----------------------------------------------------

STUCK_MAX_MINUTES: Final = 10  # FR-10.5: a first step the person takes fits in 10 minutes


@dataclass(frozen=True, slots=True)
class StuckRefusal:
    code: str
    status: int
    detail: str


def stuck_step_refusal(
    stuck_task_id: UUID,
    parent_id: UUID | None,
    label: Label | None,
    estimate_minutes: int | None,
    steps_before: int,
) -> StuckRefusal | None:
    """Why a task made with a stuck run's token is refused (P4-02); None when allowed. The
    run posts one subtask, under the stuck task (`steps_before` counts the tasks the run
    already made); a step the person takes (any estimate given, so every Human or Hybrid
    step) fits in STUCK_MAX_MINUTES; an AI step carries no estimate (FR-3.1)."""
    if parent_id != stuck_task_id:
        return StuckRefusal(
            "stuck_step_parent", 422, "A stuck run's step is a subtask of the stuck task"
        )
    if steps_before > 0:
        return StuckRefusal("stuck_step_taken", 409, "A stuck run posts one first step")
    if (
        label is not Label.AI
        and estimate_minutes is not None
        and (estimate_minutes > STUCK_MAX_MINUTES)
    ):
        return StuckRefusal(
            "stuck_step_too_long",
            422,
            f"A first step takes {STUCK_MAX_MINUTES} minutes or less; split it smaller",
        )
    return None


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


# --- Review queue order (P1-13, FR-6.1, FR-11.4, R-04) -----------------------------------------

ImpactScope = Literal["task", "project", "workspace"]
ReviewAction = Literal["accept", "edit", "reject", "snooze", "answer", "approve", "deny"]  # R-04
PRIMARY_ACTIONS: Final[tuple[ReviewAction, ...]] = ("accept", "approve", "answer")  # Enter
MINUTES_PER_TASK_EQUIVALENT: Final = 30  # plan default: 30 human minutes count like one task
JEV_APPLIED: Final = "applied"  # decisions' Route.APPLY value
JEV_MIDDLE_LEVEL: Final = 2  # of the five blocking-impact levels, 0 to 4
JEV_STEP: Final = 0.25  # plan default: the factor runs 0.5 to 1.5 over the five levels


@dataclass(frozen=True, slots=True)
class GraphTask:
    """What `downstream` needs from one live task."""

    id: UUID
    parent_id: UUID | None
    status: Status
    label: Label | None
    estimate_minutes: int | None
    due_on: date | None = None


@dataclass(frozen=True, slots=True)
class TaskGraph:
    """The live tasks of the scope a review item's impact reads (the caller loads them:
    the project's for "task" and "project", every task for "workspace")."""

    tasks: tuple[GraphTask, ...]


def _counts(tasks: Iterable[GraphTask]) -> tuple[int, int]:
    open_tasks = [task for task in tasks if task.status is not Status.DONE]
    minutes = sum(task.estimate_minutes or 0 for task in open_tasks if task.label in HUMAN_TIME)
    return len(open_tasks), minutes


def scope_tasks(
    target_task_id: UUID | None, scope: ImpactScope, graph: TaskGraph
) -> list[GraphTask]:
    """The tasks an item's impact reads, Done ones included: "task" = the target task and
    its descendants (none without a target task in `graph`); "project" and "workspace" =
    every task in `graph`."""
    if scope != "task":
        return list(graph.tasks)
    children: dict[UUID | None, list[GraphTask]] = {}
    by_id: dict[UUID, GraphTask] = {}
    for task in graph.tasks:
        children.setdefault(task.parent_id, []).append(task)
        by_id[task.id] = task
    root = by_id.get(target_task_id) if target_task_id is not None else None
    if root is None:
        return []
    subtree, stack = [], [root]
    while stack:
        task = stack.pop()
        subtree.append(task)
        stack.extend(children.get(task.id, ()))
    return subtree


def downstream(
    target_task_id: UUID | None, scope: ImpactScope, graph: TaskGraph
) -> tuple[int, int]:
    """(open task count, sum of estimate_minutes of open Human/Hybrid tasks) over the kind's
    impact scope (`scope_tasks`). AI and pending tasks add to the count, never to the
    minutes."""
    return _counts(scope_tasks(target_task_id, scope, graph))


def nearest_due(target_task_id: UUID | None, scope: ImpactScope, graph: TaskGraph) -> date | None:
    """The earliest due date among the open tasks of the scope (Jev's `nearest_due_in_days`
    input); None when none of them is dated."""
    dates = [
        task.due_on
        for task in scope_tasks(target_task_id, scope, graph)
        if task.status is not Status.DONE and task.due_on is not None
    ]
    return min(dates, default=None)


def deterministic_impact(tasks: int, minutes: int) -> float:
    return tasks + minutes / MINUTES_PER_TASK_EQUIVALENT


class ScoreLike(Protocol):
    """decisions' ScoreAnswer as the factor reads it (tasks cannot import decisions)."""

    @property
    def score(self) -> float: ...


def jev_factor(answer: ScoreLike | None, route: str | None) -> float:
    """1.0 unless the blocking-impact decision applied (`route` is decisions' Route value
    "applied"); then 1 + 0.25 * (score - 2), i.e. 0.5 to 1.5 over the five levels."""
    if answer is None or route != JEV_APPLIED:
        return 1.0
    return 1 + JEV_STEP * (answer.score - JEV_MIDDLE_LEVEL)


@dataclass(frozen=True, slots=True)
class ReviewRow:
    """What the queue order needs from one open item."""

    id: UUID
    created_at: datetime
    impact: float  # deterministic_impact of what it blocks
    jev_factor: float = 1.0


def review_key(row: ReviewRow) -> tuple[float, datetime, UUID]:
    """The biggest weighted impact first, then the oldest, then the id."""
    return (-(row.impact * row.jev_factor), row.created_at, row.id)


def review_order(rows: Sequence[ReviewRow]) -> list[ReviewRow]:
    """Sort key (-deterministic_impact * jev_factor, created_at, id)."""
    return sorted(rows, key=review_key)


class KindActions(Protocol):
    @property
    def actions(self) -> tuple[str, ...]: ...


def primary_action(spec: KindActions) -> str:
    """The first of accept, approve, answer present in spec.actions (Enter key, R-04); a
    kind with none of them has its first action."""
    for action in PRIMARY_ACTIONS:
        if action in spec.actions:
            return action
    return spec.actions[0]


# --- Taint (P2-08, SAF-1, FR-4.5, design decision 14) --------------------------------------

TaintKind = Literal["context_item", "parent_task", "run", "document", "proposal", "user"]


@dataclass(frozen=True)
class TaintSource:
    """One thing a record is made from: a linked context item, the parent task, the
    creating run, a document, a proposal, or the person who wrote it."""

    kind: TaintKind
    id: UUID | None
    tainted: bool


def derive_taint(sources: Iterable[TaintSource]) -> bool:
    """A new record's taint: tainted when any of its sources is (none: untainted). Stored
    once, in the record's own transaction; never recomputed."""
    return any(s.tainted for s in sources)


def raise_only(current: bool, incoming: bool) -> bool:
    """A record's taint after new input arrives: linking can add taint, and unlinking (or
    marking a document trusted later) never clears it."""
    return current or incoming


@dataclass(frozen=True)
class TaskView:
    """What the unattended rule reads of a task (P4-04 adds its checks on these)."""

    tainted: bool
    label: Label | None = None
    status: Status = Status.BACKLOG


def may_run_unattended(task: TaskView) -> bool:
    """False for every tainted task (SAF-1, FR-4.5): tainted work never runs in an
    unattended window. P4-04 adds the green-light and label checks on top of this function;
    it never removes this one."""
    return not task.tainted
