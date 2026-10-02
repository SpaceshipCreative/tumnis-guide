"""The `focus-wake` test tick (P2-15, R-37; fakes only): `POST /v1/test/tick/focus-wake`
sends `{"kind": "tick", "now": <the server clock>}` to every waiting focus workflow, which
then looks at that time (its due check-in or planned event fires once that time reaches
it). Registered at import; the router imports this module so the api process has it.

A focus workflow starts asynchronously (a task entering In progress: its outbox row, the
relay, the `focus.track_session` delivery, then the enqueue), so a tick fired right after
the write that starts one would miss it (A2.6 presses Start and moves the clock at once).
The tick first settles, at most SETTLE_S: until no outbox row is unsent and no delivery to
a focus subscriber is still queued or running. Then it lists the waiting workflows."""

import asyncio
import logging
from datetime import datetime
from typing import Any, Final

from sqlalchemy import func, select

from tumnis.core import audit, db
from tumnis.core.outbox import outbox_table
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.ticks import register_tick
from tumnis.core.types import SYSTEM_ACTOR

_log = logging.getLogger(__name__)

TICK_NAME: Final = "focus-wake"
TOPIC: Final = "focus"  # workflows.TOPIC
WORKFLOWS: Final = ["focus_plan", "focus_session"]
WAITING: Final = ["ENQUEUED", "PENDING"]
DELIVERY: Final = "deliver_event"  # tumnis.core.events.deliver_event's workflow name
FOCUS_DELIVERY: Final = ":focus."  # in a delivery's workflow id, `<event id>:focus.<name>`
SETTLE_S: float = 5.0  # under the e2e request timeout (10 s); read per call
POLL_S: Final = 0.05


async def _unsent() -> int:
    """Outbox rows the relay has not sent yet, every workspace's (the app role sees a
    workspace's rows only with it in context)."""
    async with db.app_sessionmaker()() as s, s.begin():
        workspace_ids = list(await audit.workspace_ids(s))
    count = select(func.count()).select_from(outbox_table).where(outbox_table.c.sent_at.is_(None))
    unsent = 0
    for workspace_id in workspace_ids:
        async with tenant_session(WorkspaceContext(workspace_id, SYSTEM_ACTOR)) as s:
            unsent += int((await s.execute(count)).scalar_one())
    return unsent


async def _focus_deliveries(client: Any) -> int:
    """Deliveries to a focus subscriber still queued or running."""
    found = await client.list_workflows_async(
        name=[DELIVERY], status=WAITING, load_input=False, load_output=False
    )
    return sum(FOCUS_DELIVERY in flow.workflow_id for flow in found)


async def _settle(client: Any) -> None:
    """Wait, at most SETTLE_S, until the focus workflows a recent write starts exist."""
    deadline = asyncio.get_running_loop().time() + SETTLE_S
    while await _unsent() or await _focus_deliveries(client):
        if asyncio.get_running_loop().time() >= deadline:
            _log.info("focus-wake: events still on their way after %s s", SETTLE_S)
            return
        await asyncio.sleep(POLL_S)


async def wake(client: Any, now: datetime) -> int:
    """Settle (see the module), then send `{"kind": "tick", "now": ...}` to every waiting
    focus workflow through `client` (a DBOSClient); how many were told."""
    await _settle(client)
    found = await client.list_workflows_async(
        name=WORKFLOWS, status=WAITING, load_input=False, load_output=False
    )
    message = {"kind": "tick", "now": now.isoformat()}
    for flow in found:
        await client.send_async(flow.workflow_id, message, TOPIC)
    return len(found)


register_tick(TICK_NAME, wake)
