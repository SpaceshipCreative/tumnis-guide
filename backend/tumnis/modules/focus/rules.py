"""focus pure rules: no I/O, `now` and `tz` passed in (P2-15, stubs until implemented)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

Level = Literal["quiet", "nudge", "coach", "guardrail"]
EventKind = Literal[
    "block_start", "not_started", "check_in_due", "switched", "stuck", "block_end", "day_end"
]
Response = Literal["still_on_it", "switched", "stuck", "snooze", "less_of_this"]

LEVEL_EVENTS: Final[dict[Level, frozenset[EventKind]]] = {}
NOT_STARTED_AFTER: Final = timedelta(0)
SNOOZE_FOR: Final = timedelta(0)
DEFAULT_CADENCE_MIN: Final = 0
BACKOFF_AFTER: Final = 0
GATEABLE: Final[frozenset[EventKind]] = frozenset()


@dataclass(frozen=True)
class OverrideView:
    day: date
    level: Level


@dataclass(frozen=True)
class SessionState:
    task_id: UUID
    started_at: datetime
    base_cadence_min: int
    streak: int
    doubled: bool
    last_check_at: datetime
    snoozed_until: datetime | None


@dataclass(frozen=True)
class PlanItemView:
    task_id: UUID
    block_start: datetime | None
    block_end: datetime | None


@dataclass(frozen=True)
class PlannedEvent:
    kind: EventKind
    at: datetime
    task_id: UUID | None


@dataclass(frozen=True)
class NoulAnswer:
    p: float
    confidence: float


def fires(level: Level, kind: EventKind) -> bool:
    raise NotImplementedError


def effective_level(
    workspace: Level, override: OverrideView | None, now: datetime, tz: ZoneInfo,
    day_close_at: datetime,
) -> Level:  # fmt: skip
    raise NotImplementedError


def lower(level: Level) -> Level:
    raise NotImplementedError


def cadence(state: SessionState) -> timedelta:
    raise NotImplementedError


def apply_response(state: SessionState, response: Response, now: datetime) -> SessionState:
    raise NotImplementedError


def check_in_fired(state: SessionState, at: datetime) -> SessionState:
    raise NotImplementedError


def next_check_in(state: SessionState, level: Level) -> datetime | None:
    raise NotImplementedError


def suppressed_by_activity(
    activity_times: Sequence[datetime], window_start: datetime, now: datetime, *, signals_on: bool
) -> bool:
    raise NotImplementedError


def plan_events(items: Sequence[PlanItemView], day_end_at: datetime) -> list[PlannedEvent]:
    raise NotImplementedError


def not_started_holds(task_status: str) -> bool:
    raise NotImplementedError


def gate(
    kind: EventKind, noul: NoulAnswer | None, threshold: float | None
) -> Literal["fire", "suppress"]:
    raise NotImplementedError


def attribution(level: Level, kind: EventKind, detail: str | None = None) -> str:
    raise NotImplementedError
