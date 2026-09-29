"""`Interval` and the small pure helpers behind `free_blocks` (P1-10 refactor step):
`clip` to a window and `subtract` busy time from one."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from tumnis.modules.calendar.rules import Interval, clip, subtract

T0 = datetime(2026, 3, 9, 13, 0, tzinfo=UTC)


def _span(start_min: int, end_min: int) -> Interval:
    return Interval(T0 + timedelta(minutes=start_min), T0 + timedelta(minutes=end_min))


@pytest.mark.req("FR-1.3")
@pytest.mark.wp("P1-10")
def test_interval_is_aware_utc_and_ends_after_it_starts() -> None:
    """An Interval refuses naive or empty spans, keeps its instants in UTC and counts whole
    minutes, rounded down."""
    eastern = timezone(timedelta(hours=-4))
    local = Interval(datetime(2026, 3, 9, 9, 0, tzinfo=eastern), T0 + timedelta(hours=1))
    assert local.start == T0
    assert local.start.utcoffset() == timedelta(0)
    assert Interval(T0, T0 + timedelta(minutes=14, seconds=59)).minutes == 14
    with pytest.raises(ValueError, match="aware"):
        Interval(T0.replace(tzinfo=None), T0 + timedelta(hours=1))
    with pytest.raises(ValueError, match="ends after"):
        Interval(T0, T0)
    assert sorted([_span(30, 60), _span(0, 90), _span(0, 30)]) == [
        _span(0, 30),
        _span(0, 90),
        _span(30, 60),
    ]


@pytest.mark.req("FR-1.3")
@pytest.mark.wp("P1-10")
def test_clip_keeps_the_part_inside_the_window() -> None:
    """clip gives the overlap with the window, or None when there is none (touching is
    not overlapping)."""
    window = _span(0, 540)
    assert clip(_span(-30, 30), window) == _span(0, 30)
    assert clip(_span(500, 600), window) == _span(500, 540)
    assert clip(_span(60, 120), window) == _span(60, 120)
    assert clip(_span(-60, 0), window) is None
    assert clip(_span(540, 600), window) is None


@pytest.mark.req("FR-1.3")
@pytest.mark.wp("P1-10")
def test_subtract_gives_every_gap_merging_overlapping_busy_time() -> None:
    """subtract keeps every gap, however short; overlapping, touching and nested busy
    intervals merge, and busy time outside the window is ignored."""
    window = _span(0, 540)
    busy = [_span(60, 120), _span(90, 150), _span(150, 160), _span(100, 110), _span(600, 700)]
    assert subtract(window, busy) == [_span(0, 60), _span(160, 540)]
    assert subtract(window, []) == [window]
    assert subtract(window, [_span(-10, 1), _span(539, 600)]) == [_span(1, 539)]
    assert subtract(window, [_span(-10, 600)]) == []
