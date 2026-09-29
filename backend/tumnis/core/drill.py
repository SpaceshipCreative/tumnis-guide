"""Restore drill results (P0-28, REL-1): `tumnis drill record` after scripts/drill/restore_drill.sh.

The thresholds live here, in code, as the A0.4 acceptance test re-checks them: the point
in time restored must be at most 15 minutes old (RPO) and the restore through its checks
must take at most an hour (RTO).
"""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Final, Literal

from sqlalchemy.ext.asyncio import AsyncEngine

from tumnis.core import ops_status

RPO_LIMIT_SECONDS: Final = 15 * 60
RTO_LIMIT_SECONDS: Final = 60 * 60
CHECK: Final = "restore_drill"
DrillMode = Literal["prod", "rehearsal"]


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
    """Upsert ops_status "restore_drill" with the numbers.

    P0-15 adds the audit row here (`audit.record(..., "drill.completed", details=...)`, in
    the same transaction): the audit log does not exist before it lands.
    """
    async with engine.begin() as conn:
        await ops_status.record(conn, CHECK, ok=result.ok, checked_at=now, details=result.details())
