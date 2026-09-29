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
@pytest.mark.xfail(strict=True, reason="spec:P0-28")
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
