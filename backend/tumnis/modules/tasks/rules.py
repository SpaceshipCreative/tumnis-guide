"""tasks pure rules: no I/O, `now` and `tz` passed in (P0-18).

Interfaces only until the P0-18 spec tests turn green.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Final
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


class ActorKind(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    SYSTEM = "system"


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


@dataclass(frozen=True, slots=True)
class Edge:
    trigger: Trigger
    actors: frozenset[ActorKind]
    labels: frozenset[Label | None] = frozenset([*Label, None])


TRANSITIONS: Final[Mapping[tuple[Status, Status], Edge]] = {}


class TransitionNotAllowed(Exception):  # noqa: N818  # the plan's name
    code = "transition_not_allowed"


def check_transition(frm: Status, to: Status, actor: ActorKind, label: Label | None) -> Edge:
    raise NotImplementedError


@dataclass(frozen=True, slots=True)
class TaskState:
    status: Status
    label: Label | None
    rollover_count: int
    started_at: datetime | None
    completed_at: datetime | None
    actual_minutes: int | None


def apply_transition(s: TaskState, to: Status, actor: ActorKind, now: datetime) -> TaskState:
    raise NotImplementedError


class EstimateRequired(Exception):  # noqa: N818  # the plan's name
    code = "estimate_required"


def normalize_estimate(label: Label | None, estimate: int | None, actor: ActorKind) -> int | None:
    raise NotImplementedError


class Placement(StrEnum):
    CARD = "card"
    NESTED = "nested"


def subtask_placement(
    label: Label | None, estimate_minutes: int | None, threshold_min: int
) -> Placement:
    raise NotImplementedError


@dataclass(frozen=True, slots=True)
class TodayTask:
    id: UUID
    priority: str
    due_on: date | None
    created_at: datetime


def today_order(tasks: Sequence[TodayTask]) -> list[TodayTask]:
    raise NotImplementedError
