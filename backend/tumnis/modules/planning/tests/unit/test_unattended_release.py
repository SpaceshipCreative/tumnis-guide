"""When an unattended run may start and when its results reach you (P4-04, FR-4.5): a run
is not started with less than half the project's maximum run time left in the window, and
the overnight batch is released 15 minutes before the first working-hours start after the
window, never before the window ends."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from tumnis.modules.planning.rules import (
    batch_release_at,
    next_working_start,
    too_late_to_start,
)

NEW_YORK = ZoneInfo("America/New_York")


def _local(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=NEW_YORK).astimezone(UTC)


@pytest.mark.req("FR-4.5")
@pytest.mark.wp("P4-04")
def test_too_late_to_start_at_half_the_max_run_time() -> None:
    end = _local("2026-03-10T06:00:00")
    assert too_late_to_start(end - timedelta(minutes=29), end, 60)
    assert not too_late_to_start(end - timedelta(minutes=30), end, 60)
    assert too_late_to_start(end, end, 60)


@pytest.mark.req("FR-4.5")
@pytest.mark.wp("P4-04")
def test_batch_releases_before_work_but_never_inside_the_window() -> None:
    end = _local("2026-03-10T06:00:00")
    assert batch_release_at(end, _local("2026-03-10T09:00:00")) == _local("2026-03-10T08:45:00")
    assert batch_release_at(end, _local("2026-03-10T06:10:00")) == end


@pytest.mark.req("FR-4.5")
@pytest.mark.wp("P4-04")
def test_next_working_start_skips_the_weekend_and_a_started_day() -> None:
    # Tuesday 06:00: that day's default hours start at 09:00.
    assert next_working_start(_local("2026-03-10T06:00:00"), NEW_YORK, {}) == _local(
        "2026-03-10T09:00:00"
    )
    # Friday 10:00 has already started; the next start is Monday 09:00.
    assert next_working_start(_local("2026-03-13T10:00:00"), NEW_YORK, {}) == _local(
        "2026-03-16T09:00:00"
    )
    # A workspace's own hours for Monday.
    hours = {0: (time(7, 30), time(15, 0))}
    assert next_working_start(_local("2026-03-14T23:00:00"), NEW_YORK, hours) == _local(
        "2026-03-16T07:30:00"
    )


@pytest.mark.req("FR-4.5")
@pytest.mark.wp("P4-04")
def test_next_working_start_without_any_window_is_the_moment_itself() -> None:
    empty = {day: (time(9), time(9)) for day in range(5)}
    after = _local("2026-03-10T06:00:00")
    assert next_working_start(after, NEW_YORK, empty) == after
