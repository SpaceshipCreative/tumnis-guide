"""The focus-gating Noul (P2-15, FR-11.4): before a gateable event (not_started,
check_in_due) fires, the `nudge_warranted` Noul is asked through the Decisions slot. A
confident "no" suppresses it; with Decisions down the deterministic rule stands.

Day: Tuesday 2026-03-10 in New York (the `workspace` fixture), level Coach.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.focus.tests.integration._focus import (
    TUESDAY,
    at,
    decisions_down,
    nudge_noul,
    rows,
)

if TYPE_CHECKING:
    from tumnis.modules.focus.tests.integration._focus import Focus

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-11.4")
@pytest.mark.wp("P2-15")
async def test_noul_suppresses_and_decisions_down_fires(dbos: Any, focus: Focus) -> None:
    """T-P2-15-13
    The fake Decisions answers that a nudge is not warranted (p = 0.05): the check-in at
    +25 minutes is suppressed (no row, no `focus.event`) and the decision is logged at the
    `nudge_warranted` point with the event kind and no task text. With Decisions down, the
    next check-in, a full cadence later, fires.
    """
    task = await focus.task("Write proposal", first_action="Open the proposal outline")
    await focus.level("coach")
    started = at(TUESDAY, "10:00")
    await focus.advance(started)
    await focus.move(task, "in_progress")

    focus.use_decisions(nudge_noul(0.05))
    await focus.advance(started + timedelta(minutes=25))
    assert focus.events() == []
    assert focus.payloads("focus.event") == []
    [logged] = rows(
        focus.db,
        "SELECT fields_sent FROM decision_log WHERE decision_point = 'nudge_warranted'",
    )
    assert "event_kind" in logged["fields_sent"]
    assert not {"title", "task_title", "first_action"} & set(logged["fields_sent"])

    focus.use_decisions(decisions_down())
    await focus.advance(started + timedelta(minutes=49))
    assert focus.events() == []
    await focus.advance(started + timedelta(minutes=50))
    [fired] = focus.events()
    assert (fired["kind"], fired["fired_at"]) == ("check_in_due", started + timedelta(minutes=50))
    assert len(focus.payloads("focus.event")) == 1
