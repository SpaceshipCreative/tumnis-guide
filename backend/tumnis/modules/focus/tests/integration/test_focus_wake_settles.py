"""The `focus-wake` tick reaches a session that is still starting (A2.6, journey J8).

A task entering In progress starts its `focus_session` asynchronously: the status change's
outbox row, the relay, the `focus.track_session` delivery, then the enqueue. A journey that
presses Start and moves the clock at once fires the tick inside that window. The tick
waits for the focus deliveries to land before it lists the waiting workflows, so the new
session hears it and its first check-in fires on time.

Day: Tuesday 2026-03-10 in New York (the `workspace` fixture).
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest

from tests._labels import relay_running
from tumnis.modules.focus.tests.integration._focus import TUESDAY, at

if TYPE_CHECKING:
    from tumnis.modules.focus.tests.integration._focus import Focus

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

CADENCE = timedelta(minutes=25)  # the project's default focus cadence


@pytest.mark.req("FR-10.2")
@pytest.mark.wp("P2-15")
async def test_a_tick_right_after_start_reaches_the_new_session(dbos: Any, focus: Focus) -> None:
    """At Coach, a task moved to In progress at 10:00 and a `focus-wake` tick at 10:25 sent
    at once, while the relay runs as in the worker: the tick reaches the new session's
    workflow, and its check_in_due fires at 10:25."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    task = await focus.task("Write proposal", first_action="Open the proposal outline")
    await focus.level("coach")
    started = at(TUESDAY, "10:00")
    await focus.advance(started)

    async with relay_running():
        ctx = focus.ctx()
        async with tenant_session(ctx) as s:  # the Start button's write, no settling after
            row = await tasks.get_task(s, task)
            await tasks.change_status(
                s, ctx.actor, task, tasks.Status("in_progress"), row.version, now=started
            )
        await focus.set_clock(started + CADENCE)
        answer = await focus.http.post("/v1/test/tick/focus-wake")
        await focus.settle()

    assert answer.status_code == 200, answer.text
    assert answer.json()["woken"] == 1
    check_ins = focus.events("check_in_due")
    assert [e["fired_at"] for e in check_ins] == [started + CADENCE]
    assert check_ins[0]["task_id"] == task
