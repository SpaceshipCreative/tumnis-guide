"""Operations workflows (P0-28): the `*/15` backup freshness check on the maintenance queue.

The check reads WAL archiving from `pg_stat_archiver` and the last successful backup per
(repo, type) from `ops_backup_runs`, applies the pure rule in core/backups.py, and upserts
ops_status "backups". Readiness shows a failing check as `backups: degraded` (never down),
and the ops gauges export it (core/metrics.py).
"""

from datetime import datetime
from typing import Any, Final, Protocol

from dbos import DBOS
from sqlalchemy import text

from tumnis.core import db, ops_status
from tumnis.core.backups import BackupFacts, backup_findings

MAINTENANCE_QUEUE: Final = "maintenance"
BACKUP_FRESHNESS_SCHEDULE: Final = "*/15 * * * *"  # UTC (A9)
CHECK: Final = "backups"


class BackupFactsSource(Protocol):
    async def read(self, now: datetime) -> BackupFacts: ...


class DatabaseBackupFacts:
    """The facts from the database this worker runs against (direct URL, app role)."""

    async def read(self, now: datetime) -> BackupFacts:
        async with db.direct_engine().connect() as conn:
            archiver = (
                await conn.execute(
                    text("SELECT last_archived_time, last_failed_time FROM pg_stat_archiver")
                )
            ).one()
            runs = await conn.execute(
                text(
                    "SELECT repo, type, max(finished_at) FROM ops_backup_runs "
                    "WHERE ok GROUP BY repo, type"
                )
            )
            last_ok = {(int(repo), str(kind)): at for repo, kind, at in runs}
        archived, failed = archiver.last_archived_time, archiver.last_failed_time
        return BackupFacts(
            now=now,
            wal_last_archived_at=archived,
            wal_failed_since_last_success=failed is not None
            and (archived is None or failed > archived),
            last_ok=last_ok,
        )


_source: BackupFactsSource = DatabaseBackupFacts()


def set_backup_facts_source(source: BackupFactsSource | None) -> BackupFactsSource | None:
    """Swap the facts source (tests script a fake; None restores the database source);
    returns the previous one."""
    global _source  # noqa: PLW0603  # one process-wide source, swapped only by tests
    previous = _source
    _source = source if source is not None else DatabaseBackupFacts()
    return previous


@DBOS.step()
async def check_backups(now: datetime) -> list[str]:
    facts = await _source.read(now)
    findings = backup_findings(facts)
    async with db.direct_engine().begin() as conn:
        await ops_status.record(
            conn, CHECK, ok=not findings, checked_at=now, details={"findings": findings}
        )
    return findings


@DBOS.workflow()
async def backup_freshness_check(scheduled_at: datetime, context: Any) -> None:
    """Scheduled `*/15 * * * *` on the maintenance queue (DBOS passes the scheduled time and
    the schedule's context); `scheduled_at` is the time the rule judges against."""
    await check_backups(scheduled_at)
