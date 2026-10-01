"""usage pure rules: no I/O, `now` and `tz` passed in.

The counter map (P0-21): which event raises which per-workspace counter, by how much, on
which day. Each emitting work package adds its event's line to `COUNTERS`; the usage
subscribers follow the map (one per key).
"""

from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, date, datetime
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


def daily_open_rate(open_days: set[date], start: date, end: date) -> float | None:
    raise NotImplementedError  # P1-18


def tasks_completed_per_working_day(
    done_at: Sequence[datetime], tz: ZoneInfo, start: date, end: date, weekdays: frozenset[int]
) -> float | None:
    raise NotImplementedError  # P1-18


def rollover_rate(planned: Sequence[PlannedOutcome]) -> float | None:
    raise NotImplementedError  # P1-18


def estimate_error(pairs: Sequence[tuple[int, int]]) -> float | None:
    raise NotImplementedError  # P1-18


def consecutive_plan_days(
    days: Mapping[date, PlanDayFacts], end: date, weekdays: frozenset[int]
) -> int:
    raise NotImplementedError  # P1-18
