"""usage pure rules: no I/O, `now` and `tz` passed in.

The counter map (P0-21): which event raises which per-workspace counter, by how much, on
which day. Each emitting work package adds its event's line to `COUNTERS`; the usage
subscribers follow the map (one per key).
"""

from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel

CounterFn = Callable[[Mapping[str, Any]], int]
Counters = tuple[tuple[str, CounterFn], ...]


class _ReadOnly(Mapping[str, Counters]):
    """A mapping nothing can change after import (`types.MappingProxyType` is outside the
    rules allow-list)."""

    def __init__(self, items: Mapping[str, Counters]) -> None:
        self._items = dict(items)

    def __getitem__(self, key: str) -> Counters:
        return self._items[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)


def _one(_payload: Mapping[str, Any]) -> int:
    return 1


def _field(name: str) -> CounterFn:
    """The payload's non-negative integer `name` (0 when absent or not a count)."""

    def amount(payload: Mapping[str, Any]) -> int:
        value = payload.get(name, 0)
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0

    return amount


COUNTERS: Final[Mapping[str, Counters]] = _ReadOnly(
    {
        "task.created": (("tasks_created", _one),),
        "project.created": (("projects_created", _one),),
        "decision.made": (("decisions", _one),),  # P1-02
        # P2-13: GitHub requests made by a pull request refresh, and how many of them GitHub
        # answered 304 Not Modified (those do not count against its rate limit)
        "github.fetched": (
            ("github_requests", _field("requests")),
            ("github_not_modified", _field("not_modified")),
        ),
        # added by later WPs: run.finished -> runs, run_minutes (ceil(duration_s / 60));
        # items.ingested -> ingested_items (len(item_ids))
    }
)


def increments(event_name: str, payload: Mapping[str, Any]) -> list[tuple[str, int]]:
    """(counter, amount) for each counter the event raises; [] for an event not in the map.
    Zero amounts are dropped: they would write a ledger row that counts nothing."""
    return [
        (counter, amount)
        for counter, fn in COUNTERS.get(event_name, ())
        if (amount := fn(payload)) != 0
    ]


def usage_day(occurred_at: datetime) -> date:
    """The UTC date the event counts on (plan default: UTC, not the workspace timezone).
    A naive datetime is refused: its day would depend on the host's timezone."""
    if occurred_at.tzinfo is None:
        raise ValueError("occurred_at must be timezone-aware")
    return occurred_at.astimezone(UTC).date()


# --- Local success metrics (P1-18, PRD Success metrics) ----------------------------------------


class PlannedOutcome(BaseModel, frozen=True):
    """A task that was on a published plan, and how many nights it has rolled over."""

    task_id: UUID
    rollover_count: int


class PlanDayFacts(BaseModel, frozen=True):
    """One local day of the exit gate: a plan was published, and the human accepted,
    swapped or removed at least one of its items."""

    published: bool
    decided: bool


ROLLOVER_LIMIT: Final = 2  # PRD: "tasks carried more than 2 days"
_DAY: Final = timedelta(days=1)


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def daily_open_rate(open_days: set[date], start: date, end: date) -> float | None:
    """Share of the local days `start` to `end` (both inclusive) the app was opened on
    (target 5 of 7). None with no opens at all, or an empty range: no data yet."""
    days = _days(start, end)
    if not days or not open_days:
        return None
    return sum(1 for day in days if day in open_days) / len(days)


def tasks_completed_per_working_day(
    done_at: Sequence[datetime], tz: ZoneInfo, start: date, end: date, weekdays: frozenset[int]
) -> float | None:
    """Tasks completed on the local days `start` to `end` (any day, weekends too) per
    working day in the range (`weekdays`, 0 = Monday); compared with the 2-week baseline.
    None with no completions or no working day in the range."""
    working = sum(1 for day in _days(start, end) if day.weekday() in weekdays)
    done = sum(1 for at in done_at if start <= at.astimezone(tz).date() <= end)
    if working == 0 or not done_at:
        return None
    return done / working


def rollover_rate(planned: Sequence[PlannedOutcome]) -> float | None:
    """Share of planned tasks whose rollover_count exceeded 2 (carried more than 2 days);
    None if none planned."""
    if not planned:
        return None
    return sum(1 for p in planned if p.rollover_count > ROLLOVER_LIMIT) / len(planned)


def estimate_error(pairs: Sequence[tuple[int, int]]) -> float | None:
    """Median of |actual - estimate| / estimate over completed Human and Hybrid tasks,
    given as (estimate, actual) minutes; a zero estimate has no ratio and is left out.
    None with no pair left. (`statistics` is outside the rules allow-list.)"""
    errors = sorted(abs(actual - estimate) / estimate for estimate, actual in pairs if estimate > 0)
    if not errors:
        return None
    mid = len(errors) // 2
    return errors[mid] if len(errors) % 2 else (errors[mid - 1] + errors[mid]) / 2


def consecutive_plan_days(
    days: Mapping[date, PlanDayFacts], end: date, weekdays: frozenset[int]
) -> int:
    """Working days ending at `end` in a row where a plan was published and at least one plan
    item was accepted, swapped or removed by the human (the phase 1 exit gate). Days outside
    `weekdays` are skipped; a working day without both breaks the run."""
    if not days or not weekdays:
        return 0
    first = min(days)
    count, day = 0, end
    while day >= first:
        if day.weekday() in weekdays:
            facts = days.get(day)
            if facts is None or not (facts.published and facts.decided):
                break
            count += 1
        day -= _DAY
    return count
