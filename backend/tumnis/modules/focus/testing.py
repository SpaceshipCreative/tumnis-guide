"""The `focus-wake` test tick (P2-15, R-37; fakes only): `POST /v1/test/tick/focus-wake`
sends `{"kind": "tick", "now": <the server clock>}` to every waiting focus workflow, which
then looks at that time (its due check-in or planned event fires once that time reaches
it). Registered at import; the router imports this module so the api process has it."""

from datetime import datetime
from typing import Any, Final

from tumnis.core.ticks import register_tick

TICK_NAME: Final = "focus-wake"
TOPIC: Final = "focus"  # workflows.TOPIC
WORKFLOWS: Final = ["focus_plan", "focus_session"]
WAITING: Final = ["ENQUEUED", "PENDING"]


async def wake(client: Any, now: datetime) -> int:
    """Send `{"kind": "tick", "now": ...}` to every waiting focus workflow through `client`
    (a DBOSClient); how many were told."""
    found = await client.list_workflows_async(
        name=WORKFLOWS, status=WAITING, load_input=False, load_output=False
    )
    message = {"kind": "tick", "now": now.isoformat()}
    for flow in found:
        await client.send_async(flow.workflow_id, message, TOPIC)
    return len(found)


register_tick(TICK_NAME, wake)
