"""`focus_session` sleeps durably (P2-15, FR-10.2, R-30): its wait for the next check-in is a
`recv` with a timeout, so a killed worker's session resumes in the next worker and fires
each check-in once; and it ends when its task leaves In progress.

Day: Tuesday 2026-03-10 in New York (the `workspace` fixture), level Coach.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.focus.tests.integration._focus import TUESDAY, at, until

if TYPE_CHECKING:
    from tests.fixtures import WorkerKillerFactory
    from tumnis.modules.focus.tests.integration._focus import Focus

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-10.2")
@pytest.mark.wp("P2-15")
@pytest.mark.slow
@pytest.mark.xfail(strict=True, reason="spec:P2-15")
async def test_session_survives_restart(worker_killer: WorkerKillerFactory, focus: Focus) -> None:
    """T-P2-15-11
    The worker is killed while the session of the started task waits for its first
    check-in; a fresh worker recovers it. A tick at +25 minutes fires one check_in_due,
    and a second tick at the same time fires nothing more.
    """
    from tests.fixtures import KILLED_EXIT  # noqa: PLC0415

    point = "focus.session.waiting"
    killer = worker_killer(point, events=0)
    task = await focus.task("Write proposal")
    await focus.level("coach")
    focus.forget_setup()
    started = at(TUESDAY, "10:00")

    armed = await killer.start(point)
    try:
        await focus.move(task, "in_progress", at_time=started)
        code = await asyncio.wait_for(armed.wait(), 60)
    except TimeoutError:
        await killer.stop(armed)
        pytest.fail(f"worker not killed at {point}\n{killer.log_tail()}")
    assert code == KILLED_EXIT, killer.log_tail()
    assert focus.events() == []

    worker = await killer.start(None)
    try:
        due = started + timedelta(minutes=25)
        await focus.wake_through(killer.dbos_client(), due)
        assert await until(lambda: len(focus.events("check_in_due")) == 1), killer.log_tail()
        await focus.wake_through(killer.dbos_client(), due)
        await asyncio.sleep(3)
    finally:
        await killer.stop(worker)

    [check_in] = focus.events("check_in_due")
    assert check_in["fired_at"] == due
    assert check_in["task_id"] == task
    assert len(focus.sessions()) == 1


@pytest.mark.req("FR-10.2")
@pytest.mark.wp("P2-15")
@pytest.mark.slow
@pytest.mark.xfail(strict=True, reason="spec:P2-15")
async def test_session_ends_when_task_leaves_in_progress(dbos: Any, focus: Focus) -> None:
    """T-P2-15-12
    On the real clock, with the session's wait cut to 2 seconds (R-30: the timeout path,
    no tick), the started task's session fires check-ins on its own; moving the task to
    In review ends the session (`ended_at` set, workflow finished) and nothing fires after.
    """
    task = await focus.task("Write proposal")
    await focus.level("coach")
    focus.short_waits(2)
    await focus.move(task, "in_progress")
    assert await until(lambda: len(focus.events("check_in_due")) >= 1, timeout_s=30)

    await focus.move(task, "in_review")
    fired = len(focus.events())
    [session] = focus.sessions()
    assert await until(lambda: focus.sessions()[0]["ended_at"] is not None, timeout_s=15)
    assert await until(lambda: focus.workflow_status(session["workflow_id"]) == "SUCCESS")
    await asyncio.sleep(5)
    assert len(focus.events()) == fired
    assert {e["kind"] for e in focus.events()} == {"check_in_due"}
