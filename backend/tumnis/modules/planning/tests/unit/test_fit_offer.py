"""A task with no big enough gap gets an offer (P1-11, J6): split it into chunks that fit
today's blocks, or move it to the first working day ahead with a block as long as the
task. Blocks are given by length; the day is Monday 2026-03-09, Tuesday is 2026-03-10."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from tumnis.core.types import Interval
from tumnis.modules.planning import rules

MONDAY = date(2026, 3, 9)
TUESDAY = date(2026, 3, 10)


def blocks(day: date, *minutes: int) -> list[Interval]:
    """Blocks of these lengths on `day`, starting 09:00 UTC, three hours apart."""
    start = datetime(day.year, day.month, day.day, 9, tzinfo=UTC)
    out = []
    for n, length in enumerate(minutes):
        begin = start + timedelta(hours=3 * n)
        out.append(Interval(begin, begin + timedelta(minutes=length)))
    return out


def task(estimate: int) -> Any:
    return rules.PlanTask(
        task_id=UUID("0199aa00-0000-7000-8000-0000000000f1"),
        project_id=UUID("0199aa00-0000-7000-8000-000000000001"),
        label="human",
        estimate_minutes=estimate,
        status="backlog",
        blocked=False,
        due_on=None,
        priority=1,
        rollover_count=0,
        created_at=datetime(2026, 3, 1, tzinfo=UTC),
        eligible=True,
    )


ROWS: list[Any] = [
    pytest.param(90, [60, 45], {}, [60, 30], None, id="split_into_two_blocks"),
    pytest.param(90, [60], {TUESDAY: [120]}, [60, 30], TUESDAY, id="split_or_move_tuesday"),
    pytest.param(90, [20], {}, None, None, id="nothing_fits"),
    pytest.param(20, [15], {}, None, None, id="chunks_never_below_fifteen"),
    pytest.param(90, [80], {}, [75, 15], None, id="last_chunk_raised_to_fifteen"),
    pytest.param(
        90,
        [30],
        {TUESDAY: [60], date(2026, 3, 11): [90, 30]},
        None,
        date(2026, 3, 11),
        id="move_to_first_day_with_room",
    ),
]


@pytest.mark.req("J6")
@pytest.mark.wp("P1-11")
@pytest.mark.parametrize(("estimate", "today", "ahead", "split", "move_to"), ROWS)
def test_fit_offer_table(
    estimate: int,
    today: list[int],
    ahead: dict[date, list[int]],
    split: list[int] | None,
    move_to: date | None,
) -> None:
    """T-P1-11-05
    90 min vs blocks [60, 45] -> split [60, 30]; vs [60] and Tue 120 -> split [60, 30] and
    move Tue; vs [20] and none ahead -> split None, move None; chunks never below 15.
    """
    offer = rules.fit_offer(
        task(estimate),
        blocks(MONDAY, *today),
        {day: blocks(day, *lengths) for day, lengths in ahead.items()},
    )
    assert offer.split == split
    assert offer.move_to == move_to
    if offer.split is not None:
        assert sum(offer.split) == estimate
        assert all(chunk >= rules.MIN_SPLIT_CHUNK for chunk in offer.split)
