"""planning pure rules: no I/O, `now` and `tz` passed in.

- `working_window`: a day's working hours in the workspace timezone as a UTC `Interval`
  (P1-10, FR-4.7, REL-6).
"""

from collections.abc import Mapping
from datetime import date, datetime, time
from typing import Final
from zoneinfo import ZoneInfo

from tumnis.core.types import Interval

DEFAULT_HOURS: Final = (time(9, 0), time(18, 0))  # FR-4.7


def working_window(
    day: date, tz: ZoneInfo, hours: Mapping[int, tuple[time, time]], *, replan: bool
) -> Interval | None:
    """Weekday with a row -> that row. Saturday or Sunday -> None, unless replan ->
    DEFAULT_HOURS (plan YAML, P1-10 red test: weekends have no window unless Re-plan)."""
    raise NotImplementedError


def local_to_utc(day: date, local_time: time, tz: ZoneInfo) -> datetime:
    """`core.clock.local_to_utc` (R-12)."""
    raise NotImplementedError
