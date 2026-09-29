"""local_to_utc: DST gaps shift forward, ambiguous times take the first occurrence (R-12)."""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from tumnis.core.clock import local_to_utc

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
