"""Free blocks: the working window minus every busy event, from any account (P1-10,
FR-1.3, FR-4.7).

Properties under Hypothesis: a window of 1 to 14 hours in 2026 (UTC), up to 40 busy
intervals that may overlap each other and lie partly or wholly outside the window, and a
minimum block length of 0 to 60 minutes. Times are whole minutes, so the window can be
checked minute by minute. The rules are imported inside each test, so this file collects
before they exist.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

if TYPE_CHECKING:
    from tumnis.modules.calendar.rules import Interval

MINUTE = timedelta(minutes=1)


def _interval(start: datetime, end: datetime) -> Interval:
    from tumnis.modules.calendar.rules import Interval  # noqa: PLC0415

    return Interval(start, end)


@st.composite
def _cases(draw: st.DrawFn) -> tuple[Interval, list[Interval], int]:
    start = draw(
        st.datetimes(
            min_value=datetime(2026, 1, 1),  # noqa: DTZ001  # Hypothesis wants naive bounds
            max_value=datetime(2026, 12, 31),  # noqa: DTZ001
            timezones=st.just(UTC),
        )
    ).replace(second=0, microsecond=0)
    length = draw(st.integers(60, 14 * 60))
    window = _interval(start, start + length * MINUTE)
    spans = draw(
        st.lists(
            st.tuples(st.integers(-240, length + 240), st.integers(1, 8 * 60)),
            max_size=40,
        )
    )
    busy = [_interval(start + at * MINUTE, start + (at + span) * MINUTE) for at, span in spans]
    return window, busy, draw(st.integers(0, 60))


def _minutes(interval: Interval, origin: datetime) -> set[int]:
    """The minutes an interval covers, counted from `origin`."""
    first = int((interval.start - origin) / MINUTE)
    last = int((interval.end - origin) / MINUTE)
    return set(range(first, last))


def _runs(minutes: set[int]) -> list[set[int]]:
    """The maximal runs of consecutive minutes."""
    runs: list[set[int]] = []
    for minute in sorted(minutes):
        if runs and minute - 1 in runs[-1]:
            runs[-1].add(minute)
        else:
            runs.append({minute})
    return runs


@pytest.mark.req("FR-1.3")
@pytest.mark.wp("P1-10")
@pytest.mark.xfail(strict=True, reason="spec:P1-10")
@settings(max_examples=300, deadline=None)
@given(_cases())
def test_never_overlaps_busy(case: tuple[Interval, list[Interval], int]) -> None:
    """T-P1-10-01
    No free block intersects any busy interval, whichever account the busy events come
    from and however they overlap each other.
    """
    from tumnis.modules.calendar.rules import free_blocks  # noqa: PLC0415

    window, busy, min_minutes = case
    blocks = free_blocks(window, busy, min_minutes=min_minutes)
    for block in blocks:
        for event in busy:
            assert not (block.start < event.end and event.start < block.end), (block, event)


@pytest.mark.req("FR-4.7")
@pytest.mark.wp("P1-10")
@pytest.mark.xfail(strict=True, reason="spec:P1-10")
@settings(max_examples=300, deadline=None)
@given(_cases())
def test_inside_window(case: tuple[Interval, list[Interval], int]) -> None:
    """T-P1-10-02
    Every block lies within the window and is a proper interval in UTC; no window (a
    weekend without Re-plan) gives no blocks.
    """
    from tumnis.modules.calendar.rules import free_blocks  # noqa: PLC0415

    window, busy, min_minutes = case
    for block in free_blocks(window, busy, min_minutes=min_minutes):
        assert window.start <= block.start < block.end <= window.end, (window, block)
        assert block.start.utcoffset() == timedelta(0)
    assert free_blocks(None, busy, min_minutes=min_minutes) == []


@pytest.mark.req("FR-1.3")
@pytest.mark.wp("P1-10")
@pytest.mark.xfail(strict=True, reason="spec:P1-10")
@settings(max_examples=300, deadline=None)
@given(_cases())
def test_merged_and_sorted(case: tuple[Interval, list[Interval], int]) -> None:
    """T-P1-10-03
    Blocks come sorted, pairwise disjoint and never touching (adjacent gaps are merged),
    each at least `min_minutes` long.
    """
    from tumnis.modules.calendar.rules import free_blocks  # noqa: PLC0415

    window, busy, min_minutes = case
    blocks = free_blocks(window, busy, min_minutes=min_minutes)
    assert blocks == sorted(blocks)
    for earlier, later in itertools.pairwise(blocks):
        assert earlier.end < later.start, (earlier, later)
    assert all(block.minutes >= min_minutes for block in blocks)


@pytest.mark.req("FR-1.3")
@pytest.mark.wp("P1-10")
@pytest.mark.xfail(strict=True, reason="spec:P1-10")
@settings(max_examples=300, deadline=None)
@given(_cases())
def test_accounts_for_whole_window(case: tuple[Interval, list[Interval], int]) -> None:
    """T-P1-10-04
    Minute by minute, the window is exactly the free blocks, the busy time inside it and
    the gaps dropped for being shorter than `min_minutes`, with no minute counted twice.
    """
    from tumnis.modules.calendar.rules import free_blocks  # noqa: PLC0415

    window, busy, min_minutes = case
    origin = window.start
    whole = _minutes(window, origin)
    taken = set().union(*(_minutes(event, origin) for event in busy)) & whole
    dropped = set().union(*(run for run in _runs(whole - taken) if len(run) < min_minutes))
    free = [_minutes(block, origin) for block in free_blocks(window, busy, min_minutes=min_minutes)]

    parts = [*free, taken, dropped]
    assert set().union(*parts) == whole
    assert sum(len(part) for part in parts) == len(whole)


def _at(hhmm: str) -> datetime:
    hours, minutes = hhmm.split(":")
    return datetime(2026, 3, 9, int(hours), int(minutes), tzinfo=UTC)


# The window of Monday 2026-03-09 in New York (09:00 to 18:00 EDT), in UTC.
WINDOW = ("13:00", "22:00")
EXAMPLES: dict[str, dict[str, Any]] = {
    "empty_day": {"busy": [], "free": [WINDOW]},
    "fully_booked": {"busy": [("12:00", "23:00")], "free": []},
    "back_to_back_meetings": {
        "busy": [("14:00", "15:00"), ("15:00", "16:00")],
        "free": [("13:00", "14:00"), ("16:00", "22:00")],
    },
    "overlapping_accounts": {
        "busy": [("14:00", "15:30"), ("15:00", "16:00")],
        "free": [("13:00", "14:00"), ("16:00", "22:00")],
    },
    "meeting_across_window_edges": {
        "busy": [("12:30", "13:30"), ("21:30", "22:30")],
        "free": [("13:30", "21:30")],
    },
    "fourteen_minute_gap_dropped": {
        "busy": [("13:00", "14:00"), ("14:14", "15:00")],
        "free": [("15:00", "22:00")],
    },
    "fifteen_minute_gap_kept": {
        "busy": [("13:00", "14:00"), ("14:15", "15:00")],
        "free": [("14:00", "14:15"), ("15:00", "22:00")],
    },
}


@pytest.mark.req("FR-1.3")
@pytest.mark.wp("P1-10")
@pytest.mark.xfail(strict=True, reason="spec:P1-10")
@pytest.mark.parametrize("case", sorted(EXAMPLES))
def test_examples(case: str) -> None:
    """T-P1-10-05
    A hand table over one working window with the default 15-minute minimum: an empty day,
    a fully booked one, back-to-back meetings, two accounts' overlapping meetings, meetings
    across the window's edges, a 14-minute gap dropped and a 15-minute gap kept.
    """
    from tumnis.modules.calendar.rules import MIN_BLOCK_MINUTES, free_blocks  # noqa: PLC0415

    example = EXAMPLES[case]
    window = _interval(*map(_at, WINDOW))
    busy = [_interval(_at(start), _at(end)) for start, end in example["busy"]]
    expected = [_interval(_at(start), _at(end)) for start, end in example["free"]]

    assert MIN_BLOCK_MINUTES == 15
    blocks = free_blocks(window, busy)
    assert blocks == expected
    assert [block.minutes for block in blocks] == [
        int((block.end - block.start) / MINUTE) for block in expected
    ]
