"""The next instance of a recurring task comes on done or on due date passed, never both
(P0-19, FR-3.5).

`successor(spec, latest, latest_done, trigger, now, tz)` is asked only about a rule's
latest instance. A model replays random done and tick events against it and keeps the
instances; the invariants below must hold for every sequence. The rules are imported
lazily, so the file collects before they exist.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

START = datetime(2026, 3, 1, tzinfo=UTC)
SPAN_MINUTES = 20 * 7 * 24 * 60  # twenty weeks
ZONES = ("America/New_York", "Australia/Sydney")


def _rr() -> Any:
    from tumnis.modules.tasks import rules_recurrence  # noqa: PLC0415

    return rules_recurrence


EVENTS = st.lists(
    st.tuples(st.sampled_from(["done", "tick"]), st.integers(0, SPAN_MINUTES)),
    min_size=1,
    max_size=40,
    unique_by=lambda event: event[1],
).map(lambda events: sorted(events, key=lambda event: event[1]))


@pytest.mark.req("FR-3.5")
@pytest.mark.wp("P0-19")
@settings(max_examples=200, deadline=None)
@given(
    zone=st.sampled_from(ZONES),
    weekday=st.integers(0, 6),
    due=st.sampled_from([time(9, 0), time(2, 30), time(1, 30), time(23, 45)]),
    events=EVENTS,
)
def test_done_or_due_never_both(
    zone: str, weekday: int, due: time, events: list[tuple[str, int]]
) -> None:
    """T-P0-19-08
    A weekly rule in either zone, 1 to 40 done and tick events at increasing times over 20
    weeks from 2026-03-01T00:00Z. Each instance gets at most one successor; occurrences
    strictly increase and never repeat; every successor is after the event that made it; a
    done instance never gets a second successor from a later tick.
    """
    rr = _rr()
    tz = ZoneInfo(zone)
    spec = rr.RecurrenceSpec(rr.Preset.WEEKLY, None, weekday=weekday, due_time=due)
    instances: list[list[Any]] = [[rr.next_occurrence(spec, START, tz), False]]  # [occ, done]
    successors = [0]  # successors made per instance

    for kind, minutes in events:
        now = START + timedelta(minutes=minutes)
        latest = instances[-1]
        if kind == "done":
            if latest[1]:
                continue  # done applies to an open latest instance only
            latest[1] = True
            made = rr.successor(spec, latest[0], True, "done", now, tz)
            assert made is not None
        else:
            made = rr.successor(spec, latest[0], latest[1], "tick", now, tz)
        # a done instance never also gets a successor from a tick
        for occurrence, done in instances[:-1]:
            if done:
                assert rr.successor(spec, occurrence, True, "tick", now, tz) is None
        if made is None:
            continue
        assert made > now
        successors[-1] += 1
        instances.append([made, False])
        successors.append(0)

    occurrences = [occurrence for occurrence, _ in instances]
    assert occurrences == sorted(occurrences)
    assert len(set(occurrences)) == len(occurrences)
    assert all(count <= 1 for count in successors)


@pytest.mark.req("FR-3.5")
@pytest.mark.wp("P0-19")
def test_missed_weeks_create_one_instance() -> None:
    """T-P0-19-09
    Given a weekly Monday rule whose latest instance was due 2026-03-02 (09:00 EST) and is
    open, when the first tick after it runs at 2026-03-24T12:00Z, then exactly one instance
    is created, due Monday 2026-03-30 09:00 EDT; nothing for Mar 9, 16 or 23, and a second
    tick at the same time makes nothing more.
    """
    rr = _rr()
    tz = ZoneInfo("America/New_York")
    spec = rr.RecurrenceSpec(rr.Preset.WEEKLY, None, weekday=0)
    latest = datetime(2026, 3, 2, 14, 0, tzinfo=UTC)
    now = datetime(2026, 3, 24, 12, 0, tzinfo=UTC)

    made = rr.successor(spec, latest, False, "tick", now, tz)
    assert made == datetime(2026, 3, 30, 13, 0, tzinfo=UTC)
    assert made.astimezone(tz).date().isoformat() == "2026-03-30"
    assert rr.successor(spec, made, False, "tick", now, tz) is None
    # before the latest instance is due, a tick makes nothing
    assert rr.successor(spec, latest, False, "tick", latest - timedelta(minutes=1), tz) is None
