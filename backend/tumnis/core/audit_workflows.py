"""The nightly audit chain verify (P0-15, SEC-3, REL-5).

`audit_verify` runs at 03:23 UTC (plan default) on the maintenance queue. For each
workspace it runs `verify_chain`; a clean chain gets an anchor at its head, a broken one
gets none and is logged with its breaks. Two gauges per workspace carry the result:
`tumnis_audit_chain_ok` (1 clean, 0 broken; P0-27's alert fires on 0) and
`tumnis_audit_anchored_seq` (the head anchored by the last clean run; truncation after it
shows as the gauge going down). The gauges live in the worker's process, so the run also
upserts ops_status "audit_chain" (P0-28's table), which the api's scrape-time ops gauges
and any readiness check read.
"""

import logging
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from dbos import DBOS
from prometheus_client import Gauge

from tumnis.core import audit, db, ops_status
from tumnis.core.metrics import REGISTRY
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR

AUDIT_VERIFY_SCHEDULE: Final = "23 3 * * *"  # UTC, plan default
SCHEDULE_NAME: Final = "audit-verify"
CHECK: Final = "audit_chain"

log = logging.getLogger(__name__)

AUDIT_CHAIN_OK = Gauge(
    "tumnis_audit_chain_ok",
    "1 when the workspace's audit chain verified clean on the last nightly run, 0 when broken",
    ["workspace"],
    registry=REGISTRY,
)
AUDIT_ANCHORED_SEQ = Gauge(
    "tumnis_audit_anchored_seq",
    "Head seq of the workspace's audit chain anchored by the last clean nightly run",
    ["workspace"],
    registry=REGISTRY,
)


@DBOS.step()
async def list_workspaces() -> list[str]:
    async with db.app_sessionmaker()() as s, s.begin():
        return [str(workspace_id) for workspace_id in await audit.workspace_ids(s)]


@DBOS.step()
async def verify_and_anchor(workspace_id: str, now: datetime) -> tuple[bool, int | None]:
    """Verify one workspace's chain and anchor its head when clean; returns (clean, the
    anchored head seq or None)."""
    ws = UUID(workspace_id)
    async with tenant_session(WorkspaceContext(ws, SYSTEM_ACTOR)) as s:
        breaks = await audit.verify_chain(s, ws)
        if breaks:
            log.warning(
                "audit chain broken",
                extra={"workspace_id": workspace_id, "breaks": [(b.seq, b.kind) for b in breaks]},
            )
            return False, None
        await audit.anchor(s, ws, now)
        current = await audit.head(s, ws)
    return True, current[0] if current else None


@DBOS.step()
async def record_status(broken: list[str], checked: int, now: datetime) -> None:
    async with db.app_engine().begin() as conn:
        await ops_status.record(
            conn,
            CHECK,
            ok=not broken,
            checked_at=now,
            details={"checked": checked, "broken": broken},
        )


@DBOS.workflow()
async def audit_verify(scheduled_at: datetime, context: Any) -> None:
    """Scheduled `23 3 * * *` on the maintenance queue (DBOS passes the scheduled time and
    the schedule's context); `scheduled_at` is the anchors' time."""
    workspaces = await list_workspaces()
    broken: list[str] = []
    for workspace_id in workspaces:
        clean, anchored = await verify_and_anchor(workspace_id, scheduled_at)
        AUDIT_CHAIN_OK.labels(workspace=workspace_id).set(1 if clean else 0)
        if anchored is not None:
            AUDIT_ANCHORED_SEQ.labels(workspace=workspace_id).set(anchored)
        if not clean:
            broken.append(workspace_id)
    await record_status(broken, len(workspaces), scheduled_at)
