"""How a running `dispatch_run` hears about its run (P2-04, R-30): `run.signal` delivery.

The api, the runner's handler and the sweeps never call DBOS for a run: they emit
`run.signal` (`api.signal_run`, `api.cancel_run`, `api.accept_result`), and the
`agents.deliver_run_signal` subscriber hands it to the run's workflow here, once per event
(the event id is the send's idempotency key). The workflow reads each message on the run's
topic with `signal_of`, which also understands the older shapes still sent there.

Split from workflows.py; the supervision loop that reads these stays there.
"""

from typing import Any
from uuid import UUID

from dbos import DBOS
from dbos._error import DBOSNonExistentWorkflowError  # dbos 3.1.0: not re-exported
from sqlalchemy import Table, select

from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.agents import api
from tumnis.modules.agents.models import RunRow

_runs: Table = RunRow.__table__  # type: ignore[assignment]


def signal_of(message: dict[str, Any]) -> tuple[str, str | None]:
    """A message on the run's topic as (kind, reason): `run.signal` deliveries, and the
    older shapes (`{"status": "runner_lost"}` from a run_skill-era sweep, `{"status":
    "cancelled"}` from the protocol-1 cancel fallback)."""
    kind = message.get("kind")
    if isinstance(kind, str):
        return kind, message.get("reason")
    status = message.get("status")
    if status == "runner_lost":
        return "runner_lost", api.RUNNER_LOST
    if status == "cancelled":
        return "cancel", message.get("error") or "cancelled"
    return "ignored", None


async def deliver_signal(
    workspace_id: UUID, run_id: UUID, kind: str, reason: str | None, key: str
) -> None:
    """Send a `run.signal` to the run's workflow, once per event (`key`). A workflow that
    does not exist yet is retried (the delivery raises), unless the run already ended."""
    message = {"kind": kind, "reason": reason}
    async with tenant_session(WorkspaceContext(workspace_id, SYSTEM_ACTOR)) as s:
        row = (
            await s.execute(select(_runs.c.status, _runs.c.workflow_id).where(_runs.c.id == run_id))
        ).first()
    if row is None:
        return
    target = row.workflow_id or api.dispatch_workflow_id(run_id)
    try:
        await DBOS.send_async(target, message, topic=api.run_topic(run_id), idempotency_key=key)
    except DBOSNonExistentWorkflowError:
        if row.status in api.TERMINAL:
            return
        raise
