"""Focus events from the workflows on a fixed server clock (P2-15, FR-10.2): `focus_plan`
fires the day's planned events and `focus_session` the check-ins of an In progress task,
each at the level in force. The clock moves through `POST /v1/test/clock`, and the
`focus-wake` tick (`POST /v1/test/tick/focus-wake`) makes the waiting workflows look at the
new time.

Day: Tuesday 2026-03-10 in New York (the `workspace` fixture), working hours 09:00 to 18:00.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.focus.tests.integration._focus import TUESDAY, at

if TYPE_CHECKING:
    from tumnis.modules.focus.tests.integration._focus import Focus

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

FULL_DAY = ("08:45", "09:59", "10:00", "10:14", "10:15", "10:30", "10:49", "10:50", "12:00",
            "17:59", "18:00", "20:00")  # fmt: skip


@pytest.mark.req("FR-10.2")
@pytest.mark.wp("P2-15")
@pytest.mark.xfail(strict=True, reason="spec:P2-15")
async def test_nudge_fires_block_start_not_started_day_end(dbos: Any, focus: Focus) -> None:
    """T-P2-15-08
    At Nudge, a day with `Write proposal` blocked 10:00 to 10:50 that is never started
    fires exactly block_start at 10:00, not_started at 10:15 and day_end at 18:00, each
    once, attributed to Nudge; the block_start message carries the task's first action.
    """
    task = await focus.task("Write proposal", first_action="Open the proposal outline")
    await focus.advance(at(TUESDAY, "08:30"))
    await focus.publish(TUESDAY, [(task, "10:00", "10:50")])
    await focus.level("nudge")
    for hhmm in FULL_DAY:
        await focus.advance(at(TUESDAY, hhmm))
    await focus.advance(at(TUESDAY, "20:00"))  # a second tick at the same time fires nothing

    assert focus.kinds() == [
        ("block_start", at(TUESDAY, "10:00")),
        ("not_started", at(TUESDAY, "10:15")),
        ("day_end", at(TUESDAY, "18:00")),
    ]
    events = focus.events()
    assert {e["level"] for e in events} == {"nudge"}
    assert [e["task_id"] for e in events[:2]] == [task, task]
    assert "Open the proposal outline" in events[0]["message"]
    assert events[0]["rule"] == "Nudge · block_start"


@pytest.mark.req("FR-10.2")
@pytest.mark.wp("P2-15")
@pytest.mark.xfail(strict=True, reason="spec:P2-15")
async def test_coach_adds_check_in_switched_stuck(dbos: Any, focus: Focus) -> None:
    """T-P2-15-09
    At Coach the planned events fire as at Nudge, and the session adds its kinds: the task
    started at 10:05 gets check_in_due at 10:30 (25-minute cadence, no not_started since it
    is In progress at 10:15), answering stuck fires stuck, and starting another task while
    the session runs fires switched. Every event is attributed to Coach.
    """
    task = await focus.task("Write proposal", first_action="Open the proposal outline")
    other = await focus.task("Send the invoice")
    await focus.advance(at(TUESDAY, "08:30"))
    await focus.publish(TUESDAY, [(task, "10:00", "10:50")])
    await focus.level("coach")
    await focus.advance(at(TUESDAY, "10:00"))
    await focus.advance(at(TUESDAY, "10:05"))
    await focus.move(task, "in_progress")
    for hhmm in ("10:15", "10:29", "10:30"):
        await focus.advance(at(TUESDAY, hhmm))
    assert len(focus.events("check_in_due")) == 1

    answered = await focus.respond("check_in_due", "stuck")
    assert answered.status_code == 200, answered.text
    await focus.advance(at(TUESDAY, "10:40"))
    await focus.move(other, "in_progress")

    kinds = [k for k, _ in focus.kinds()]
    assert kinds == ["block_start", "check_in_due", "stuck", "switched"]
    assert focus.events("check_in_due")[0]["fired_at"] == at(TUESDAY, "10:30")
    assert focus.events("switched")[0]["task_id"] == other
    assert focus.events("stuck")[0]["task_id"] == task
    assert {e["level"] for e in focus.events()} == {"coach"}
    assert focus.events("check_in_due")[0]["rule"].startswith("Coach · check_in_due")


@pytest.mark.req("FR-10.2")
@pytest.mark.wp("P2-15")
@pytest.mark.xfail(strict=True, reason="spec:P2-15")
async def test_quiet_fires_nothing(dbos: Any, focus: Focus) -> None:
    """T-P2-15-10
    At Quiet (the default) a full planned day, with the task started and later another
    task started, writes no `focus_events` row and emits no `focus.event`.
    """
    task = await focus.task("Write proposal", first_action="Open the proposal outline")
    other = await focus.task("Send the invoice")
    await focus.advance(at(TUESDAY, "08:30"))
    await focus.publish(TUESDAY, [(task, "10:00", "10:50")])
    current = await focus.current()
    assert current["level"] == "quiet"
    for hhmm in FULL_DAY[:4]:
        await focus.advance(at(TUESDAY, hhmm))
    await focus.move(task, "in_progress")
    started = at(TUESDAY, "10:14")
    for minutes in (25, 50, 75, 100):
        await focus.advance(started + timedelta(minutes=minutes))
    await focus.move(other, "in_progress")
    for hhmm in FULL_DAY[8:]:
        await focus.advance(at(TUESDAY, hhmm))

    assert focus.events() == []
    assert focus.payloads("focus.event") == []
