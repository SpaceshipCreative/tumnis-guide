"""Day close at local midnight in the workspace timezone (P0-19, FR-3.6, REL-6).

`next_day_close(anchor, tz)` is the first local midnight after the anchor (the start of the
next local day, which DST may move off 00:00); `day_close_due(anchor, now, tz)` names the
local date to close once `now` reaches it. The anchor is the later of the last close and
the timezone change, so a mid-day timezone change never rolls Today over in the morning.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

VECTORS = Path(__file__).resolve().parents[1] / "fixtures" / "dst_vectors.yaml"
NEW_YORK, SYDNEY = ZoneInfo("America/New_York"), ZoneInfo("Australia/Sydney")


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


@pytest.mark.req("FR-3.6", "REL-6")
@pytest.mark.wp("P0-19")
@pytest.mark.xfail(strict=True, reason="spec:P0-19")
def test_next_day_close_across_dst() -> None:
    """T-P0-19-11
    23- and 25-hour days in New York, both Sydney changes, and Santiago's spring-forward day
    whose midnight does not exist (the day starts at 01:00 -03): each anchor's next close
    matches the vector table; the day closed at that instant is the local day that ended.
    """
    from tumnis.modules.tasks.rules_recurrence import day_close_due, next_day_close  # noqa: PLC0415

    cases = yaml.safe_load(VECTORS.read_text())["day_close"]
    assert {case["zone"] for case in cases} == {
        "America/New_York",
        "Australia/Sydney",
        "America/Santiago",
    }
    for case in cases:
        tz = ZoneInfo(case["zone"])
        anchor, close = _at(case["anchor"]), _at(case["next_close"])
        assert next_day_close(anchor, tz) == close, case["note"]
        assert day_close_due(anchor, close - timedelta(seconds=1), tz) is None
        ended = close.astimezone(tz).date().toordinal() - 1
        assert day_close_due(anchor, close, tz) == date.fromordinal(ended), case["note"]

    # the 23-hour day: a New York close at 2026-03-08 00:00 EST closes Mar 7; the next,
    # 23 hours later, closes Mar 8
    assert day_close_due(_at("2026-03-08T03:00Z"), _at("2026-03-08T05:00Z"), NEW_YORK) == date(
        2026, 3, 7
    )
    assert day_close_due(_at("2026-03-08T05:00Z"), _at("2026-03-09T04:00Z"), NEW_YORK) == date(
        2026, 3, 8
    )


@pytest.mark.req("FR-3.6", "REL-6")
@pytest.mark.wp("P0-19")
@pytest.mark.xfail(strict=True, reason="spec:P0-19")
def test_timezone_change_moves_next_close() -> None:
    """T-P0-19-12
    Given a Sydney close at 2026-03-07T13:00Z, when the workspace switches to New York at
    2026-03-08T12:00Z (the anchor becomes that instant), then nothing is due at 12:05Z (no
    08:05 local rollover) and the next close is 2026-03-09T04:00Z. The reverse switch (New
    York to Sydney at the same instant) gives the next close 2026-03-08T13:00Z. A missed
    day closes once: days later, the one due date is the local day that just ended.
    """
    from tumnis.modules.tasks.rules_recurrence import day_close_due, next_day_close  # noqa: PLC0415

    sydney_close = _at("2026-03-07T13:00Z")
    switched = _at("2026-03-08T12:00Z")
    anchor = max(sydney_close, switched)

    assert day_close_due(anchor, _at("2026-03-08T12:05Z"), NEW_YORK) is None
    assert next_day_close(anchor, NEW_YORK) == _at("2026-03-09T04:00Z")
    assert day_close_due(anchor, _at("2026-03-09T03:59Z"), NEW_YORK) is None
    assert day_close_due(anchor, _at("2026-03-09T04:00Z"), NEW_YORK) == date(2026, 3, 8)

    assert next_day_close(switched, SYDNEY) == _at("2026-03-08T13:00Z")
    assert day_close_due(switched, _at("2026-03-08T12:05Z"), SYDNEY) is None
    assert day_close_due(switched, _at("2026-03-08T13:00Z"), SYDNEY) == date(2026, 3, 8)

    # the worker was down for three days: one close, for the day that just ended
    assert day_close_due(anchor, _at("2026-03-12T12:00Z"), NEW_YORK) == date(2026, 3, 11)
