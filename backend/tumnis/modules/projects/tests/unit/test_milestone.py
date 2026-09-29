"""The next milestone a project shows (P0-17, FR-1.1): the earlier of its deadline and the
next open task due on or after today."""

from __future__ import annotations

from datetime import date

import pytest

from tumnis.modules.projects.rules import next_milestone

TODAY = date(2026, 3, 9)


@pytest.mark.req("FR-1.1")
@pytest.mark.wp("P0-17")
@pytest.mark.parametrize(
    ("deadline", "next_due", "expected"),
    [
        (None, None, None),
        (date(2026, 4, 1), None, date(2026, 4, 1)),
        (None, date(2026, 3, 12), date(2026, 3, 12)),
        (date(2026, 4, 1), date(2026, 3, 12), date(2026, 3, 12)),
        (date(2026, 3, 10), date(2026, 3, 12), date(2026, 3, 10)),
        (None, TODAY, TODAY),
        (None, date(2026, 3, 1), None),
        (date(2026, 3, 1), None, date(2026, 3, 1)),
    ],
)
def test_next_milestone_is_the_earlier_upcoming_date(
    deadline: date | None, next_due: date | None, expected: date | None
) -> None:
    assert next_milestone(deadline, next_due, TODAY) == expected
