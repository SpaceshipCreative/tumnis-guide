"""usage pure rules: no I/O, `now` and `tz` passed in.

The counter map (P0-21): which event raises which per-workspace counter, by how much, on
which day.
"""

from collections.abc import Callable, Mapping
from datetime import date, datetime
from typing import Any, Final

CounterFn = Callable[[Mapping[str, Any]], int]

COUNTERS: Final[Mapping[str, tuple[tuple[str, CounterFn], ...]]] = {}  # spec stub (P0-21)


def increments(event_name: str, payload: Mapping[str, Any]) -> list[tuple[str, int]]:
    raise NotImplementedError("P0-21")


def usage_day(occurred_at: datetime) -> date:
    raise NotImplementedError("P0-21")
