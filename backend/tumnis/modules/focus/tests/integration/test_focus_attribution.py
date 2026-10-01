"""Rule attribution and "less of this" (P2-15, FR-10.9): every focus event names the level
and rule that produced it, and "less of this" lowers today's level until the day closes.

Days: Tuesday and Wednesday 2026-03-10/11 in New York (the `workspace` fixture).
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.focus.tests.integration._focus import TUESDAY, WEDNESDAY, at

if TYPE_CHECKING:
    from tumnis.modules.focus.tests.integration._focus import Focus

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

ATTRIBUTION = re.compile(r"^(Quiet|Nudge|Coach|Guardrail) · [a-z_]+")


@pytest.mark.req("FR-10.9")
@pytest.mark.wp("P2-15")
@pytest.mark.xfail(strict=True, reason="spec:P2-15")
async def test_every_event_has_level_and_rule(dbos: Any, focus: Focus) -> None:
    """T-P2-15-14
    A Coach day with a planned block, a started task, a check-in, an answer and the task
    put back to Backlog before the day ends: every `focus_events` row and every
    `focus.event` payload carries the level and the rule ('Coach · check_in_due (25 min
    cadence)'), and the payloads match the rows one to one.
    """
    task = await focus.task("Write proposal", first_action="Open the proposal outline")
    await focus.advance(at(TUESDAY, "08:30"))
    await focus.publish(TUESDAY, [(task, "10:00", "10:50")])
    await focus.level("coach")
    await focus.advance(at(TUESDAY, "10:00"))
    await focus.move(task, "in_progress")
    await focus.advance(at(TUESDAY, "10:25"))
    await focus.respond("check_in_due", "stuck")
    await focus.advance(at(TUESDAY, "10:40"))
    await focus.move(task, "backlog")
    await focus.advance(at(TUESDAY, "18:00"))

    events = focus.events()
    payloads = focus.payloads("focus.event")
    assert {e["kind"] for e in events} == {"block_start", "check_in_due", "stuck", "day_end"}
    assert len(payloads) == len(events)
    for event in events:
        assert event["level"] == "coach"
        assert ATTRIBUTION.match(event["rule"]), event["rule"]
    for payload in payloads:
        assert payload["level"] == "coach"
        assert ATTRIBUTION.match(payload["rule"]), payload["rule"]
    by_id = {str(e["id"]): e for e in events}
    for payload in payloads:
        row = by_id[payload["event_id"]]
        assert (payload["kind"], payload["rule"]) == (row["kind"], row["rule"])
    [check_in] = focus.events("check_in_due")
    assert check_in["rule"] == "Coach · check_in_due (25 min cadence)"
    current = await focus.current()
    assert all(ATTRIBUTION.match(m["rule"]) for m in current["messages"])


@pytest.mark.req("FR-10.9")
@pytest.mark.wp("P2-15")
@pytest.mark.xfail(strict=True, reason="spec:P2-15")
async def test_less_of_this_lowers_today_level(dbos: Any, focus: Focus) -> None:
    """T-P2-15-15
    At Coach, "less of this" on a check-in lowers today's level to Nudge (the response is
    recorded, `focus.level_changed` says so for today) and the next check-in does not fire;
    the next day the workspace level, Coach, is back.
    """
    task = await focus.task("Write proposal")
    await focus.level("coach")
    started = at(TUESDAY, "10:00")
    await focus.advance(started)
    await focus.move(task, "in_progress")
    await focus.advance(started + timedelta(minutes=25))
    assert len(focus.events("check_in_due")) == 1

    lowered = await focus.less("check_in_due")
    assert lowered.status_code == 200, lowered.text
    current = await focus.current()
    assert (current["level"], current["workspace_level"]) == ("nudge", "coach")
    assert [p["to"] for p in focus.payloads("focus.level_changed")][-1] == "nudge"
    assert focus.payloads("focus.level_changed")[-1]["scope"] == "today"
    assert [p["response"] for p in focus.payloads("focus.responded")] == ["less_of_this"]
    await focus.advance(started + timedelta(minutes=60))
    assert len(focus.events("check_in_due")) == 1

    await focus.advance(at(WEDNESDAY, "09:00"))
    current = await focus.current()
    assert (current["level"], current["workspace_level"]) == ("coach", "coach")
