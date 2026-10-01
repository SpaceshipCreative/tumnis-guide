"""focus pure rules: no I/O, `now` and `tz` passed in (P2-15, FR-10.1 to FR-10.9, FR-11.4).

- Which events fire at which level (`LEVEL_EVENTS`, `fires`; FR-10.2), the today override
  and "less of this" (`effective_level`, `lower`; FR-10.1, FR-10.9).
- The check-in cadence of one In progress task (`SessionState`, `cadence`,
  `apply_response`, `check_in_fired`, `next_check_in`; FR-10.4): two consecutive still-on-it
  answers double the cadence for the rest of the task; unanswered check-ins change nothing.
- Activity suppression (`suppressed_by_activity`; FR-10.7b) and the planned events of a
  day (`plan_events`, `not_started_holds`; FR-10.2).
- The Noul gate (`gate`; FR-11.4): it may only suppress, and only gateable kinds.
- Attribution (`attribution`; FR-10.9): every message names its level and rule.

`NoulAnswer` is this module's own reading of the `nudge_warranted` Noul (the probability
that a nudge is warranted now and the answer's confidence): rules may import nothing from
another module.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Final, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

Level = Literal["quiet", "nudge", "coach", "guardrail"]
EventKind = Literal[
    "block_start", "not_started", "check_in_due", "switched", "stuck", "block_end", "day_end"
]
Response = Literal["still_on_it", "switched", "stuck", "snooze", "less_of_this"]

LEVELS: Final[tuple[Level, ...]] = ("quiet", "nudge", "coach", "guardrail")
_NUDGE: Final[frozenset[EventKind]] = frozenset({"block_start", "not_started", "day_end"})
_COACH: Final[frozenset[EventKind]] = _NUDGE | {"check_in_due", "switched", "stuck"}
LEVEL_EVENTS: Final[dict[Level, frozenset[EventKind]]] = {
    "quiet": frozenset(),
    "nudge": _NUDGE,
    "coach": _COACH,
    "guardrail": _COACH | {"block_end"},
}
NOT_STARTED_AFTER: Final = timedelta(minutes=15)  # FR-10.2
SNOOZE_FOR: Final = timedelta(minutes=15)  # FR-10.4
DEFAULT_CADENCE_MIN: Final = 25  # FR-10.1
BACKOFF_AFTER: Final = 2  # FR-10.4: two consecutive still-on-it answers
GATEABLE: Final[frozenset[EventKind]] = frozenset({"not_started", "check_in_due"})
# The order planned events at the same instant fire in.
_PLANNED_ORDER: Final[dict[EventKind, int]] = {
    "block_end": 0,
    "block_start": 1,
    "not_started": 2,
    "day_end": 3,
}


def fires(level: Level, kind: EventKind) -> bool:
    return kind in LEVEL_EVENTS[level]


# --- Level ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class OverrideView:
    """Today's override: the local date it was set for and the level."""

    day: date
    level: Level


def effective_level(
    workspace: Level,
    override: OverrideView | None,
    now: datetime,
    tz: ZoneInfo,
    day_close_at: datetime,
) -> Level:
    """The today override applies only on its local date and only before that day's
    close (`day_close_at`, the next local midnight); otherwise the workspace level."""
    if override is None or now >= day_close_at or now.astimezone(tz).date() != override.day:
        return workspace
    return override.level


def lower(level: Level) -> Level:
    """ "Less of this": guardrail -> coach -> nudge -> quiet -> quiet."""
    return LEVELS[max(LEVELS.index(level) - 1, 0)]


# --- Check-ins -----------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionState:
    task_id: UUID
    started_at: datetime
    base_cadence_min: int
    streak: int  # consecutive still_on_it answers
    doubled: bool  # sticky for the rest of the task once streak reaches BACKOFF_AFTER
    last_check_at: datetime  # last check-in fired, answered or suppressed
    snoozed_until: datetime | None


def cadence(state: SessionState) -> timedelta:
    return timedelta(minutes=state.base_cadence_min * (2 if state.doubled else 1))


def apply_response(state: SessionState, response: Response, now: datetime) -> SessionState:
    """still_on_it: streak + 1, doubled once the streak reaches BACKOFF_AFTER, the window
    starts again now. switched, stuck: streak 0 (doubled stays), the window starts again.
    snooze: the next check-in is SNOOZE_FOR from now, streak unchanged. less_of_this
    changes the level, not the session."""
    if response == "still_on_it":
        streak = state.streak + 1
        return replace(
            state,
            streak=streak,
            doubled=state.doubled or streak >= BACKOFF_AFTER,
            last_check_at=now,
            snoozed_until=None,
        )
    if response in {"switched", "stuck"}:
        return replace(state, streak=0, last_check_at=now, snoozed_until=None)
    if response == "snooze":
        return replace(state, snoozed_until=now + SNOOZE_FOR)
    return state


def check_in_fired(state: SessionState, at: datetime) -> SessionState:
    """A check-in fired (or was suppressed) at `at`: the window starts again there and a
    snooze is used up."""
    return replace(state, last_check_at=at, snoozed_until=None)


def next_check_in(state: SessionState, level: Level) -> datetime | None:
    if not fires(level, "check_in_due"):
        return None
    if state.snoozed_until is not None:
        return state.snoozed_until  # a snooze replaces the next check-in
    return state.last_check_at + cadence(state)


def suppressed_by_activity(
    activity_times: Sequence[datetime], window_start: datetime, now: datetime, *, signals_on: bool
) -> bool:
    """FR-10.7(b): git or agent activity on the task inside [window_start, now] suppresses
    check_in_due. A suppressed check moves last_check_at to now (`check_in_fired`)."""
    return signals_on and any(window_start <= t <= now for t in activity_times)


# --- Planned events ------------------------------------------------------------------------


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


def plan_events(items: Sequence[PlanItemView], day_end_at: datetime) -> list[PlannedEvent]:
    """block_start at each block start, not_started at start + 15 min (its condition is
    checked at fire time), block_end at each block end (fires only at Guardrail), and one
    day_end at the end of working hours; in time order."""
    found: list[PlannedEvent] = [PlannedEvent("day_end", day_end_at, None)]
    for item in items:
        if item.block_start is None or item.block_end is None:
            continue
        found += [
            PlannedEvent("block_start", item.block_start, item.task_id),
            PlannedEvent("not_started", item.block_start + NOT_STARTED_AFTER, item.task_id),
            PlannedEvent("block_end", item.block_end, item.task_id),
        ]
    return sorted(found, key=lambda e: (e.at, _PLANNED_ORDER[e.kind]))


def not_started_holds(task_status: str) -> bool:
    return task_status != "in_progress"


# --- The Noul gate -------------------------------------------------------------------------


@dataclass(frozen=True)
class NoulAnswer:
    p: float  # probability that a nudge is warranted now
    confidence: float  # distance from 0.5, scaled to 0..1


def gate(
    kind: EventKind, noul: NoulAnswer | None, threshold: float | None
) -> Literal["fire", "suppress"]:
    """The Noul may only suppress, and only gateable kinds. Decisions down, no threshold,
    or confidence below threshold: the deterministic rule stands (fire)."""
    if kind not in GATEABLE or noul is None or threshold is None or noul.confidence < threshold:
        return "fire"
    return "suppress" if noul.p < 0.5 else "fire"  # noqa: PLR2004  # the Noul's midpoint


# --- Attribution ---------------------------------------------------------------------------


def attribution(level: Level, kind: EventKind, detail: str | None = None) -> str:
    """'Coach · check_in_due (50 min cadence)'; shown on every message (FR-10.9)."""
    text = f"{level.capitalize()} · {kind}"
    return text if detail is None else f"{text} ({detail})"
