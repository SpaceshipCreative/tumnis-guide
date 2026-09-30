"""planning pure rules: no I/O, `now` and `tz` passed in.

- `working_window`: a day's working hours in the workspace timezone as a UTC `Interval`
  (P1-10, FR-4.7, REL-6). Hours are local wall times per weekday (0 = Monday); a weekday
  without its own hours works the default 09:00 to 18:00, a weekend day works only on
  Re-plan.
- `local_to_utc`: `core.clock.local_to_utc` (R-12). The rules may import only pure stdlib
  (T-P0-01-09), so `core.clock` is not imported here; T-P1-10-08 pins that they agree (as
  P0-19's `rules_recurrence` does).
"""

from collections.abc import Mapping
from datetime import UTC, date, datetime, time
from typing import Final
from zoneinfo import ZoneInfo

from tumnis.core.types import Interval

DEFAULT_HOURS: Final = (time(9, 0), time(18, 0))  # FR-4.7


WEEKEND: Final = frozenset({5, 6})  # Saturday, Sunday


def working_window(
    day: date, tz: ZoneInfo, hours: Mapping[int, tuple[time, time]], *, replan: bool
) -> Interval | None:
    """Weekday with a row -> that row. Saturday or Sunday -> None, unless replan ->
    DEFAULT_HOURS (plan YAML, P1-10 red test: weekends have no window unless Re-plan).
    A weekday without a row works DEFAULT_HOURS (the workspace's hours start as the
    default), and a weekend day with a row works that row on Re-plan. Hours that a DST
    gap folds to nothing (02:30 to 03:00 on a spring-forward day) give no window."""
    weekday = day.weekday()
    if weekday in WEEKEND and not replan:
        return None
    start, end = hours.get(weekday, DEFAULT_HOURS)
    start_at, end_at = local_to_utc(day, start, tz), local_to_utc(day, end, tz)
    return Interval(start_at, end_at) if start_at < end_at else None


def local_to_utc(day: date, local_time: time, tz: ZoneInfo) -> datetime:
    """`core.clock.local_to_utc` (R-12): fold=0 moves a gap time forward by the gap and
    takes an ambiguous time's first occurrence."""
    return datetime.combine(day, local_time.replace(tzinfo=None, fold=0), tzinfo=tz).astimezone(UTC)
