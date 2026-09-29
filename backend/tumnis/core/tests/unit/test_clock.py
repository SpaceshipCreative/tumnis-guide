"""local_to_utc: DST gaps shift forward, ambiguous times take the first occurrence (R-12)."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from tumnis.core.clock import FixedClock, OverridableClock, local_to_utc

NEW_YORK = ZoneInfo("America/New_York")


@pytest.mark.req("REL-6")
@pytest.mark.wp("P0-02")
@pytest.mark.parametrize(
    ("day", "local", "expected"),
    [
        # Ordinary winter and summer times.
        (date(2026, 3, 7), time(9, 30), datetime(2026, 3, 7, 14, 30, tzinfo=UTC)),
        (date(2026, 3, 9), time(9, 30), datetime(2026, 3, 9, 13, 30, tzinfo=UTC)),
        # Spring forward: 02:30 does not exist and becomes 03:30 EDT.
        (date(2026, 3, 8), time(2, 30), datetime(2026, 3, 8, 7, 30, tzinfo=UTC)),
        # Fall back: 01:30 happens twice; the first (EDT) wins.
        (date(2026, 11, 1), time(1, 30), datetime(2026, 11, 1, 5, 30, tzinfo=UTC)),
    ],
)
def test_local_to_utc_handles_dst(day: date, local: time, expected: datetime) -> None:
    result = local_to_utc(day, local, NEW_YORK)
    assert result == expected
    assert result.tzinfo is UTC


@pytest.mark.req("REL-6")
@pytest.mark.wp("P0-02")
def test_local_to_utc_gap_reads_back_shifted_forward() -> None:
    result = local_to_utc(date(2026, 3, 8), time(2, 30), NEW_YORK)
    assert result.astimezone(NEW_YORK).time() == time(3, 30)


@pytest.mark.req("REL-6")
@pytest.mark.wp("P0-04")
def test_overridable_clock_fixes_advances_and_clears() -> None:
    """T-P0-04-17
    The test clock (issue #6) reads its base until set, stays at the set instant until
    advanced, and reads the base again once cleared; advancing an unset clock fixes it at
    the base's now first.
    """
    base = FixedClock(datetime(2026, 3, 9, 12, tzinfo=UTC))
    clock = OverridableClock(base)
    assert clock.now() == base.now()
    assert not clock.overridden

    at = datetime(2026, 3, 9, 14, tzinfo=UTC)
    assert clock.set(at) == at
    base.advance(minutes=5)
    assert clock.now() == at
    assert clock.advance(timedelta(seconds=30)) == at + timedelta(seconds=30)

    clock.clear()
    assert clock.now() == base.now()
    assert clock.advance(timedelta(minutes=1)) == base.now() + timedelta(minutes=1)
    assert clock.overridden
