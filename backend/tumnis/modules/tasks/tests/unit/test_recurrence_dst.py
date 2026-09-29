"""Recurrence next due dates across daylight-saving changes (P0-19, FR-3.5, REL-6).

The vectors live in `tests/fixtures/dst_vectors.yaml` (America/New_York and
Australia/Sydney, both directions, two years). Presets and cron are evaluated in local wall
time; a time in a spring-forward gap moves forward by the gap and an ambiguous one takes its
first occurrence (R-12). The rules are imported inside each test, so this file collects
before they exist (the spec tests are red, not collection errors).
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import yaml

VECTORS = Path(__file__).resolve().parents[1] / "fixtures" / "dst_vectors.yaml"


def _vectors(tag: str) -> list[dict[str, Any]]:
    data = yaml.safe_load(VECTORS.read_text())
    return [case for case in data["recurrence"] if tag in case["tags"]]


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def _spec(case: dict[str, Any], **overrides: Any) -> Any:
    from tumnis.modules.tasks.rules_recurrence import Preset, RecurrenceSpec  # noqa: PLC0415

    fields: dict[str, Any] = {
        "preset": Preset(case["preset"]) if case.get("preset") else None,
        "cron": case.get("cron"),
        "weekday": case.get("weekday"),
        "month_day": case.get("month_day"),
    }
    if "due_time" in case:
        fields["due_time"] = time.fromisoformat(case["due_time"])
    fields.update(overrides)
    return RecurrenceSpec(**fields)


def _walk(spec: Any, case: dict[str, Any]) -> list[datetime]:
    """next_occurrence from `after`, as many times as the case lists results."""
    from tumnis.modules.tasks.rules_recurrence import next_occurrence  # noqa: PLC0415

    tz = ZoneInfo(case["zone"])
    got, after = [], _at(case["after"])
    for _ in case["expected"]:
        after = next_occurrence(spec, after, tz)
        got.append(after)
    return got


def _check(spec: Any, case: dict[str, Any]) -> None:
    got = _walk(spec, case)
    assert got == [_at(value) for value in case["expected"]], case["case"]
    assert all(value.utcoffset() == timedelta(0) for value in got)  # aware, UTC
    local = got[0].astimezone(ZoneInfo(case["zone"])).strftime("%Y-%m-%d %H:%M %Z")
    assert local == case["local"], case["case"]


@pytest.mark.req("FR-3.5", "REL-6")
@pytest.mark.wp("P0-19")
def test_daily_preset_across_dst() -> None:
    """T-P0-19-01
    The daily preset lands at 09:00 local on both sides of all eight transitions: New York
    spring and fall, Sydney end and start, in 2026 and 2027 (23- and 25-hour gaps in UTC).
    """
    cases = _vectors("daily")
    assert len(cases) == 8
    assert {case["zone"] for case in cases} == {"America/New_York", "Australia/Sydney"}
    for case in cases:
        _check(_spec(case), case)


@pytest.mark.req("FR-3.5", "REL-6")
@pytest.mark.wp("P0-19")
def test_weekdays_preset_skips_weekend_across_dst() -> None:
    """T-P0-19-02
    Weekdays from Friday 2026-03-06 09:00 EST skip the weekend, and the US spring change
    on Sunday, to Monday 2026-03-09 09:00 EDT.
    """
    [case] = _vectors("weekdays")
    _check(_spec(case), case)


@pytest.mark.req("FR-3.5", "REL-6")
@pytest.mark.wp("P0-19")
def test_nonexistent_local_time_moves_forward() -> None:
    """T-P0-19-03
    A weekly Sunday 02:30 falls in the spring-forward gap in both zones: it fires once, at
    03:30 local (moved forward by the gap), as core.clock.local_to_utc says (R-12).
    """
    from tumnis.core.clock import local_to_utc  # noqa: PLC0415

    cases = _vectors("gap")
    assert {case["zone"] for case in cases} == {"America/New_York", "Australia/Sydney"}
    for case in cases:
        _check(_spec(case), case)
        [expected] = case["expected"]
        local = _at(expected).astimezone(ZoneInfo(case["zone"]))
        assert local_to_utc(local.date(), time(2, 30), ZoneInfo(case["zone"])) == _at(expected)


@pytest.mark.req("FR-3.5", "REL-6")
@pytest.mark.wp("P0-19")
def test_ambiguous_local_time_fires_once() -> None:
    """T-P0-19-04
    A weekly Sunday time inside the fall-back overlap fires once, at the first occurrence
    (fold=0), in both zones; the New York rule then moves on to the next Sunday
    (2026-11-08T06:30Z, 01:30 EST), never to the second 01:30 (2026-11-01T06:30Z).
    """
    cases = _vectors("overlap")
    assert {case["zone"] for case in cases} == {"America/New_York", "Australia/Sydney"}
    for case in cases:
        _check(_spec(case), case)
    ny = next(case for case in cases if case["zone"] == "America/New_York")
    assert _at("2026-11-01T06:30Z") not in _walk(_spec(ny), ny)


@pytest.mark.req("FR-3.5")
@pytest.mark.wp("P0-19")
def test_monthly_31st_clamps_to_month_end() -> None:
    """T-P0-19-05
    Monthly on the 31st clamps to the month's last day (Feb 28, Apr 30, Feb 29 in a leap
    year) and returns to the 31st where the month has one.
    """
    for case in _vectors("monthly"):
        _check(_spec(case), case)


@pytest.mark.req("FR-3.5", "REL-6")
@pytest.mark.wp("P0-19")
def test_cron_agrees_with_presets_and_handles_dst() -> None:
    """T-P0-19-06
    Cron `0 9 * * *` gives the daily rows' instants and `0 9 * * 1-5` the weekdays row's;
    `30 2 * * *` in New York moves the gap day's 02:30 to 03:30 EDT, then fires at 02:30
    EDT the next day. Cron runs on naive local wall time, then local_to_utc converts.
    """
    for case in _vectors("daily"):
        _check(_spec(case, preset=None, cron="0 9 * * *"), case)
    for case in _vectors("weekdays"):
        _check(_spec(case, preset=None, cron="0 9 * * 1-5"), case)
    for case in _vectors("cron"):
        _check(_spec(case), case)


@pytest.mark.req("FR-3.5")
@pytest.mark.wp("P0-19")
def test_invalid_spec_is_rejected() -> None:
    """T-P0-19-07
    validate_spec refuses a preset and a cron together, neither, a 6-field cron, weekly
    without a weekday, monthly without a month day and out-of-range fields, with the
    stable code `invalid_recurrence`; valid specs pass.
    """
    from tumnis.modules.tasks.rules_recurrence import (  # noqa: PLC0415
        InvalidRecurrence,
        Preset,
        RecurrenceSpec,
        validate_spec,
    )

    invalid = [
        RecurrenceSpec(Preset.DAILY, "0 9 * * *"),
        RecurrenceSpec(None, None),
        RecurrenceSpec(None, "0 0 9 * * *"),
        RecurrenceSpec(Preset.WEEKLY, None),
        RecurrenceSpec(Preset.MONTHLY, None),
        RecurrenceSpec(Preset.WEEKLY, None, weekday=7),
        RecurrenceSpec(Preset.MONTHLY, None, month_day=32),
        RecurrenceSpec(None, "61 9 * * *"),
        RecurrenceSpec(None, "every day"),
    ]
    for spec in invalid:
        with pytest.raises(InvalidRecurrence) as raised:
            validate_spec(spec)
        assert raised.value.code == "invalid_recurrence", spec
    assert InvalidRecurrence.code == "invalid_recurrence"

    for spec in [
        RecurrenceSpec(Preset.DAILY, None),
        RecurrenceSpec(Preset.WEEKDAYS, None),
        RecurrenceSpec(Preset.WEEKLY, None, weekday=0),
        RecurrenceSpec(Preset.MONTHLY, None, month_day=31, due_time=time(16, 30)),
        RecurrenceSpec(None, "0 9 * * 1-5"),
        RecurrenceSpec(None, "*/15 8-17 1,15 * 0,6"),
    ]:
        validate_spec(spec)
