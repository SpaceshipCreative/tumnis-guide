"""Restore drill results (P0-28, REL-1): `tumnis drill record` after scripts/drill/restore_drill.sh.

The thresholds live here, in code, as the A0.4 acceptance test re-checks them: the point
in time restored must be at most 15 minutes old (RPO) and the restore through its checks
must take at most an hour (RTO).

`record_drill` writes the `restore_drill` ops_status row and one `drill.completed` audit
row (P0-15) in one transaction. A drill belongs to the deployment, and an audit row to a
workspace, so it goes to the deployment's first workspace (the oldest; the only one in a
self-hosted install). A database with no workspace yet gets the ops_status row only.
"""

import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Final, Literal

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from tumnis.core import audit, ops_status
from tumnis.core.tenancy import WorkspaceContext, use_workspace
from tumnis.core.types import SYSTEM_ACTOR

RPO_LIMIT_SECONDS: Final = 15 * 60
RTO_LIMIT_SECONDS: Final = 60 * 60
CHECK: Final = "restore_drill"
DrillMode = Literal["prod", "rehearsal"]
AUDIT_ACTION: Final = "drill.completed"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DrillResult:
    mode: DrillMode
    rpo_seconds: int
    rto_seconds: int
    target: datetime

    @property
    def ok(self) -> bool:
        return 0 <= self.rpo_seconds <= RPO_LIMIT_SECONDS and (
            0 <= self.rto_seconds <= RTO_LIMIT_SECONDS
        )

    def details(self) -> dict[str, object]:
        return {
            **asdict(self),
            "target": self.target.isoformat(),
            "rpo_limit_seconds": RPO_LIMIT_SECONDS,
            "rto_limit_seconds": RTO_LIMIT_SECONDS,
        }


async def record_drill(engine: AsyncEngine, result: DrillResult, *, now: datetime) -> None:
    """Upsert ops_status "restore_drill" with the numbers and, in the same transaction,
    audit `drill.completed` with them in the deployment's first workspace."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            workspaces = await audit.workspace_ids(session)
        if not workspaces:
            log.warning("no workspace yet: drill.completed not audited")
            async with engine.begin() as conn:
                await _upsert(conn, result, now)
            return
        with use_workspace(WorkspaceContext(workspaces[0], SYSTEM_ACTOR)):
            async with session.begin():
                await _upsert(await session.connection(), result, now)
                await audit.record(session, AUDIT_ACTION, details=result.details(), occurred_at=now)


async def _upsert(conn: AsyncConnection, result: DrillResult, now: datetime) -> None:
    await ops_status.record(conn, CHECK, ok=result.ok, checked_at=now, details=result.details())
