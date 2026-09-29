"""Time for code that does I/O: `Clock` protocol, `SystemClock`, `FixedClock` (AGENTS.md, Time).

Rules never read the clock; they take `now` as an argument. Everything else asks a `Clock`.
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo


class Clock(Protocol):
    def now(self) -> datetime: ...  # always aware, UTC


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """A clock that moves only when told to; tests start it at a known instant."""

    def __init__(self, at: datetime) -> None:
        self._at = _aware_utc(at)

    def now(self) -> datetime:
        return self._at

    def advance(self, delta: timedelta | None = None, **kw: float) -> datetime:
        self._at += delta or timedelta(**kw)
        return self._at

    def set(self, at: datetime) -> None:
        self._at = _aware_utc(at)


def _aware_utc(at: datetime) -> datetime:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("FixedClock needs an aware datetime")
    return at.astimezone(UTC)


def local_to_utc(day: date, local_time: time, tz: ZoneInfo) -> datetime:
    """The UTC instant of a wall-clock time in `tz` (R-12).

    A time in a DST gap shifts forward by the gap (02:30 on a spring-forward day reads back
    as 03:30); an ambiguous time takes its first occurrence (fold=0). PEP 495 gives both:
    fold=0 resolves a gap with the offset in force before it and a repeat to the first one.
    """
    return datetime.combine(day, local_time.replace(tzinfo=None, fold=0), tzinfo=tz).astimezone(UTC)
