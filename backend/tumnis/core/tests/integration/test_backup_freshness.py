"""The backup freshness workflow (P0-28, REL-1): findings land in ops_status, readiness and
the ops gauge."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx
    from dbos import DBOS

    from tests._pg import DbUrls
    from tumnis.core.backups import BackupFacts
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


class FakeBackupFacts:
    """A facts source that returns what the test scripted."""

    def __init__(self, facts: BackupFacts) -> None:
        self.facts = facts
        self.calls: list[datetime] = []

    async def read(self, now: datetime) -> BackupFacts:
        self.calls.append(now)
        return self.facts


def _facts(now: datetime, wal_age: timedelta) -> BackupFacts:
    from tumnis.core.backups import BackupFacts  # noqa: PLC0415

    last_ok: Mapping[tuple[int, str], datetime] = {
        (1, "full"): now - timedelta(days=1),
        (1, "diff"): now - timedelta(hours=2),
        (2, "full"): now - timedelta(days=1),
        (2, "diff"): now - timedelta(hours=2),
    }
    return BackupFacts(
        now=now,
        wal_last_archived_at=now - wal_age,
        wal_failed_since_last_success=False,
        last_ok=last_ok,
    )


@pytest.mark.req("REL-1")
@pytest.mark.wp("P0-28")
async def test_freshness_marks_health_degraded(
    dbos: type[DBOS], client: httpx.AsyncClient, clock: FixedClock
) -> None:
    """T-P0-28-09
    Stale WAL from the facts source: after backup_freshness_check runs, /health/ready
    reports `backups` as degraded (still 200 overall) and tumnis_ops_check_ok{check=
    "backups"} is 0. Fresh facts on the next run turn both back to ok and 1.
    """
    from tumnis.core import db as core_db  # noqa: PLC0415
    from tumnis.core import metrics, workflows_ops  # noqa: PLC0415

    now = clock.now()
    stale = FakeBackupFacts(_facts(now, wal_age=timedelta(minutes=6)))
    previous = workflows_ops.set_backup_facts_source(stale)
    try:
        await workflows_ops.backup_freshness_check(now, None)
        assert stale.calls == [now]

        response = await client.get("/health/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["checks"]["backups"] == "degraded"
        assert body["status"] == "degraded"
        await metrics.refresh_ops_gauges(core_db.app_engine())
        sample = {"check": "backups"}
        assert metrics.REGISTRY.get_sample_value("tumnis_ops_check_ok", sample) == 0

        later = now + timedelta(minutes=15)
        workflows_ops.set_backup_facts_source(
            FakeBackupFacts(_facts(later, wal_age=timedelta(minutes=1)))
        )
        await workflows_ops.backup_freshness_check(later, None)
        response = await client.get("/health/ready")
        assert response.json()["checks"]["backups"] == "ok"
        await metrics.refresh_ops_gauges(core_db.app_engine())
        assert metrics.REGISTRY.get_sample_value("tumnis_ops_check_ok", sample) == 1
        assert metrics.REGISTRY.get_sample_value(
            "tumnis_ops_check_timestamp_seconds", sample
        ) == pytest.approx(later.timestamp())
    finally:
        workflows_ops.set_backup_facts_source(previous)


@pytest.mark.req("REL-1")
@pytest.mark.wp("P0-28")
def test_freshness_is_scheduled_on_the_maintenance_queue_in_prod(
    dbos: type[DBOS], db: DbUrls
) -> None:
    """The worker applies `backup-freshness` every 15 minutes on the maintenance queue in
    production, and no backup schedule elsewhere (previews keep no backups)."""
    from tests.fixtures import settings_for  # noqa: PLC0415
    from tumnis.core import workflows_ops  # noqa: PLC0415
    from tumnis.worker import register_schedules  # noqa: PLC0415

    register_schedules(settings_for(db, deployment_env="dev"))
    assert dbos.list_schedules() == []

    register_schedules(settings_for(db, deployment_env="prod"))
    (schedule,) = dbos.list_schedules()
    assert schedule["schedule_name"] == "backup-freshness"
    assert schedule["schedule"] == workflows_ops.BACKUP_FRESHNESS_SCHEDULE == "*/15 * * * *"
    assert schedule["queue_name"] == workflows_ops.MAINTENANCE_QUEUE == "maintenance"


@pytest.mark.req("REL-1")
@pytest.mark.wp("P0-28")
async def test_database_facts_read_archiver_and_last_successful_runs(
    db: DbUrls, clock: FixedClock
) -> None:
    """The real facts source reads pg_stat_archiver and, per (repo, type), the latest
    successful run from ops_backup_runs as the app role; failed runs do not count."""
    import psycopg  # noqa: PLC0415

    from tests._pg import OWNER  # noqa: PLC0415
    from tumnis.core import db as core_db  # noqa: PLC0415
    from tumnis.core.workflows_ops import DatabaseBackupFacts  # noqa: PLC0415

    now = clock.now()
    with psycopg.connect(db.libpq(OWNER)) as conn:
        conn.execute(
            "INSERT INTO ops_backup_runs (repo, type, started_at, finished_at, ok) VALUES "
            "(1, 'full', %(a)s, %(a)s, true), (1, 'full', %(b)s, %(b)s, false), "
            "(2, 'diff', %(a)s, %(b)s, true)",
            {"a": now - timedelta(days=2), "b": now - timedelta(hours=1)},
        )
    core_db.configure(db.app, db.app, pooled=False)
    try:
        facts = await DatabaseBackupFacts().read(now)
    finally:
        await core_db.dispose()
    assert facts.now == now
    assert facts.last_ok == {
        (1, "full"): now - timedelta(days=2),
        (2, "diff"): now - timedelta(hours=1),
    }
    # The test server does not archive: nothing archived, nothing failed.
    assert facts.wal_last_archived_at is None
    assert facts.wal_failed_since_last_success is False
