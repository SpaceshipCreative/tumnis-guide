"""How a running run's workflow hears about its run (P2-04, R-30): `run.signal` delivery.

The api, the runner's handler and the sweeps never call DBOS for a run: they emit
`run.signal` (`api.signal_run`, `api.cancel_run`, `api.accept_result`), and the
`agents.deliver_run_signal` subscriber hands it to the run's workflow here, once per event
(the event id is the send's idempotency key). The workflow reads each message on the run's
topic with `signal_of`, which also understands the older shapes still sent there.

Version-aware delivery (P2-05): DBOS recovers only the workflows of the application
version it runs, so a run whose workflow is live on an older version gets a new
supervisor on this one before the send (`stale_workflow`, then the replacement that
`agents.workflows` registers with `on_stale_workflow`, since it owns `supervise_run`).

Split from workflows.py; the supervision loop that reads these stays there.
"""

from collections.abc import Awaitable, Callable
from typing import Any, Final
from uuid import UUID

from dbos import DBOS
from dbos._error import DBOSNonExistentWorkflowError  # dbos 3.1.0: not re-exported
from sqlalchemy import Table, select
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.agents import api
from tumnis.modules.agents.models import RunRow

_runs: Table = RunRow.__table__  # type: ignore[assignment]

LIVE_WORKFLOW: Final = frozenset({"PENDING", "ENQUEUED"})

# (session holding the run row locked, workspace id, run id, stale workflow id) -> the id
# of the workflow that now supervises the run.
ReplaceSupervisor = Callable[[AsyncSession, UUID, UUID, str], Awaitable[str]]
_replace_supervisor: list[ReplaceSupervisor] = []


def on_stale_workflow(replace: ReplaceSupervisor) -> None:
    """`agents.workflows` registers how a stale run workflow is replaced, at import."""
    _replace_supervisor[:] = [replace]


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


async def stale_workflow(workflow_id: str | None) -> bool | None:
    """Whether the workflow is live on an older application version (True), live on this
    one (False); None when there is no live workflow by that id."""
    if workflow_id is None:
        return None
    status = await DBOS.get_workflow_status_async(workflow_id)
    if status is None or status.status not in LIVE_WORKFLOW:
        return None
    return status.app_version != DBOS.application_version


async def deliver_signal(
    workspace_id: UUID, run_id: UUID, kind: str, reason: str | None, key: str
) -> None:
    """Send a `run.signal` to the run's workflow, once per event (`key`). A workflow that
    does not exist yet is retried (the delivery raises), unless the run already ended.
    The run row stays locked from reading its workflow id until a stale one is replaced,
    so a concurrent delivery waits and then reads the new id instead of sending to the
    cancelled workflow."""
    message = {"kind": kind, "reason": reason}
    async with tenant_session(WorkspaceContext(workspace_id, SYSTEM_ACTOR)) as s:
        row = (
            await s.execute(
                select(_runs.c.status, _runs.c.workflow_id)
                .where(_runs.c.id == run_id)
                .with_for_update()
            )
        ).first()
        if row is None:
            return
        target = row.workflow_id or api.dispatch_workflow_id(run_id)
        if row.status not in api.TERMINAL and await stale_workflow(target):
            if not _replace_supervisor:
                raise RuntimeError("agents.workflows registers the supervisor replacement")
            target = await _replace_supervisor[0](s, workspace_id, run_id, target)
    try:
        await DBOS.send_async(target, message, topic=api.run_topic(run_id), idempotency_key=key)
    except DBOSNonExistentWorkflowError:
        if row.status in api.TERMINAL:
            return
        raise
