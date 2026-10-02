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


class OverridableClock:
    """A clock the test routes can fix (fakes only, `POST /v1/test/clock`): it reads its
    base clock until `set` fixes an instant, which then moves only with `advance`; `clear`
    goes back to the base. `create_app` wraps the app's clock in one when adapters are
    fakes, so the cache and every route read the same time. The rate limiter reads `base`:
    request rates are wall-clock, and a pinned instant would stop its refill."""

    def __init__(self, base: Clock) -> None:
        self.base = base
        self._fixed: FixedClock | None = None

    @property
    def overridden(self) -> bool:
        return self._fixed is not None

    def now(self) -> datetime:
        return (self._fixed or self.base).now()

    def set(self, at: datetime) -> datetime:
        self._fixed = FixedClock(at)
        return self._fixed.now()

    def advance(self, delta: timedelta) -> datetime:
        """Moves the fixed instant; an unfixed clock is first fixed at the base's now."""
        fixed = self._fixed or FixedClock(self.base.now())
        self._fixed = fixed
        return fixed.advance(delta)

    def clear(self) -> None:
        self._fixed = None


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
