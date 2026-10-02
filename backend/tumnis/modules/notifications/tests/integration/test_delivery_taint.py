"""Taint reaches the master's notify run (P2-16, P2-08, SAF-1): a notify packet that
carries text from a tainted task is a tainted run, whichever task the text came from."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from tumnis.modules.notifications.tests.integration._push import execute

if TYPE_CHECKING:
    from tumnis.modules.notifications.tests.integration._delivery import DeliveryWorld

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]


@pytest.mark.req("SAF-1", "FR-8.2")
@pytest.mark.wp("P2-16")
async def test_tainted_return_to_task_taints_the_notify_run(delivery: DeliveryWorld) -> None:
    """A detour's return question names the task the person left. When only that task is
    tainted, the notify packet still carries its title, so the run must be tainted."""
    delivery.master_says()
    detour = await delivery.task("Reply to the venue")
    left = await delivery.task("Write proposal")
    execute(delivery.db, "UPDATE tasks SET tainted = true WHERE id = %s", left)
    await delivery.level("coach")

    await delivery.emit(
        "focus.event",
        event_id=uuid4(),
        kind="switched",
        task_id=detour,
        rule="coach:switched",
        level="coach",
        message="Switched to: Reply to the venue",
        fired_at=delivery.now + timedelta(minutes=1),
        detour_task_id=detour,
        return_to_task_id=left,
    )

    [run] = delivery.notify_runs()
    assert run["packet"]["body"]["return_to"]["id"] == str(left)
    assert run["packet"]["tainted"] is True
