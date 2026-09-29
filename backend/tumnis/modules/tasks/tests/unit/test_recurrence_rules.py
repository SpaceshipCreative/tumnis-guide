"""Cron details and edge cases of the recurrence rules beyond the DST vectors (P0-19,
FR-3.5): month and day-of-month/day-of-week matching, steps and lists, and the specs that
can never fire."""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest

from tumnis.modules.tasks.rules_recurrence import (
    InvalidRecurrence,
    Preset,
    RecurrenceSpec,
    next_occurrence,
    validate_spec,
)

UTC_ZONE = ZoneInfo("UTC")
START = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)  # Monday


def _cron(expr: str, after: datetime = START, n: int = 1) -> list[datetime]:
    spec = RecurrenceSpec(None, expr)
    got = []
    for _ in range(n):
        after = next_occurrence(spec, after, UTC_ZONE)
        got.append(after)
    return got


@pytest.mark.req("FR-3.5")
@pytest.mark.wp("P0-19")
def test_cron_restricted_day_and_weekday_match_either() -> None:
    """Day of month 1 and Sunday (0 or 7) both restricted: either matches (Vixie cron)."""
    assert _cron("0 8 1 * 0", n=3) == [
        datetime(2026, 3, 15, 8, tzinfo=UTC),
        datetime(2026, 3, 22, 8, tzinfo=UTC),
        datetime(2026, 3, 29, 8, tzinfo=UTC),
    ]
    assert _cron("0 8 1 * 7", after=datetime(2026, 3, 29, 9, tzinfo=UTC)) == [
        datetime(2026, 4, 1, 8, tzinfo=UTC)
    ]


@pytest.mark.req("FR-3.5")
@pytest.mark.wp("P0-19")
def test_cron_months_steps_and_lists() -> None:
    """Months restrict the dates; `*/20`, `10-40/15`, `5/30` and lists give minutes in
    order; a restricted day with `*` weekday needs the day."""
    assert _cron("0 0 1 6 *") == [datetime(2026, 6, 1, tzinfo=UTC)]
    assert _cron("*/20 13 * * *", n=4) == [
        datetime(2026, 3, 9, 13, 0, tzinfo=UTC),
        datetime(2026, 3, 9, 13, 20, tzinfo=UTC),
        datetime(2026, 3, 9, 13, 40, tzinfo=UTC),
        datetime(2026, 3, 10, 13, 0, tzinfo=UTC),
    ]
    assert _cron("10-40/15 13 * * *", n=3)[-1] == datetime(2026, 3, 9, 13, 40, tzinfo=UTC)
    assert _cron("5/30 12 * * *", n=2) == [
        datetime(2026, 3, 9, 12, 5, tzinfo=UTC),
        datetime(2026, 3, 9, 12, 35, tzinfo=UTC),
    ]
    assert _cron("0 9,17 * * 1,3") == [datetime(2026, 3, 9, 17, tzinfo=UTC)]


@pytest.mark.req("FR-3.5")
@pytest.mark.wp("P0-19")
def test_specs_that_cannot_fire_or_parse_are_refused() -> None:
    """An empty range, a zero step, an offset-aware due time and a cron naming no date
    (31 February) are `invalid_recurrence`."""
    for spec in [
        RecurrenceSpec(None, "0 9 * * 5-3"),
        RecurrenceSpec(None, "*/0 9 * * *"),
        RecurrenceSpec(Preset.DAILY, None, due_time=time(9, tzinfo=UTC)),
        RecurrenceSpec(Preset.DAILY, None, weekday=1),
        RecurrenceSpec(None, "0 9 * * *", month_day=1),
    ]:
        with pytest.raises(InvalidRecurrence):
            validate_spec(spec)
    with pytest.raises(InvalidRecurrence):
        next_occurrence(RecurrenceSpec(None, "0 9 31 2 *"), START, UTC_ZONE)


@pytest.mark.req("FR-3.5")
@pytest.mark.wp("P0-19")
def test_cron_numbers_are_ascii_digits_only() -> None:
    """A cron field whose number uses non-ASCII digits (superscript two, Arabic-Indic
    three) is `invalid_recurrence`, not a bare ValueError from `int`."""
    for expr in ["0 ² * * *", "٣ 9 * * *", "0 9 1-² * *"]:
        with pytest.raises(InvalidRecurrence):
            validate_spec(RecurrenceSpec(None, expr))
