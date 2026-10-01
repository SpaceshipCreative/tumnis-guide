"""Edges of the focus rules the spec tables leave out (P2-15): "less of this" leaves the
session alone, the cadence at levels below Coach, and the attribution text."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from tumnis.modules.focus import rules

AT = datetime(2026, 3, 10, 14, 0, tzinfo=UTC)
STATE = rules.SessionState(
    task_id=UUID("0199aa00-0000-7000-8000-00000000f0c5"),
    started_at=AT,
    base_cadence_min=25,
    streak=1,
    doubled=False,
    last_check_at=AT,
    snoozed_until=None,
)


@pytest.mark.req("FR-10.9")
@pytest.mark.wp("P2-15")
def test_less_of_this_changes_the_level_not_the_session() -> None:
    assert rules.apply_response(STATE, "less_of_this", AT) == STATE


@pytest.mark.req("FR-10.2")
@pytest.mark.wp("P2-15")
@pytest.mark.parametrize("level", ["quiet", "nudge"])
def test_no_check_in_below_coach(level: rules.Level) -> None:
    assert rules.next_check_in(STATE, level) is None


@pytest.mark.req("FR-10.9")
@pytest.mark.wp("P2-15")
def test_attribution_names_level_rule_and_detail() -> None:
    assert rules.attribution("coach", "check_in_due", "50 min cadence") == (
        "Coach · check_in_due (50 min cadence)"
    )
    assert rules.attribution("nudge", "block_start") == "Nudge · block_start"
