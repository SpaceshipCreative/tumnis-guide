"""Prometheus registry (P0-27 adds the /metrics route and the request and queue metrics).

P0-08 adds the cache hit and miss counters, labelled with the registered cache name.

P0-28 adds the backup and operations gauges. They are refreshed from the database at
scrape time (one query per source), so the api reports what the worker and the backup
service recorded: `ops_status`, `ops_backup_runs` and `pg_stat_archiver`.
"""

from prometheus_client import CollectorRegistry, Counter, Gauge
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

REGISTRY = CollectorRegistry(auto_describe=True)

CACHE_HITS = Counter(
    "tumnis_cache_hits", "Cache lookups that found an entry", ["cache"], registry=REGISTRY
)
CACHE_MISSES = Counter(
    "tumnis_cache_misses", "Cache lookups that found no entry", ["cache"], registry=REGISTRY
)

OPS_CHECK_OK = Gauge(
    "tumnis_ops_check_ok",
    "1 when the operations check last passed, 0 when it failed",
    ["check"],
    registry=REGISTRY,
)
OPS_CHECK_TIMESTAMP = Gauge(
    "tumnis_ops_check_timestamp_seconds",
    "When the operations check last ran (Unix time)",
    ["check"],
    registry=REGISTRY,
)
BACKUP_LAST_SUCCESS = Gauge(
    "tumnis_backup_last_success_timestamp_seconds",
    "Finish time of the last successful pgBackRest backup per repository and type",
    ["repo", "type"],
    registry=REGISTRY,
)
WAL_LAST_ARCHIVED = Gauge(
    "tumnis_wal_last_archived_timestamp_seconds",
    "When Postgres last archived a WAL segment (pg_stat_archiver)",
    registry=REGISTRY,
)
WAL_ARCHIVE_FAILED = Gauge(
    "tumnis_wal_archive_failed_total",
    "Failed WAL archive attempts since the statistics were reset (pg_stat_archiver)",
    registry=REGISTRY,
)


async def refresh_ops_gauges(engine: AsyncEngine) -> None:
    """Set the backup and ops gauges from the database (called on each scrape)."""
    async with engine.connect() as conn:
        checks = (await conn.execute(text('SELECT "check", ok, checked_at FROM ops_status'))).all()
        runs = (
            await conn.execute(
                text(
                    "SELECT repo, type, max(finished_at) FROM ops_backup_runs "
                    "WHERE ok GROUP BY repo, type"
                )
            )
        ).all()
        archiver = (
            await conn.execute(
                text("SELECT last_archived_time, failed_count FROM pg_stat_archiver")
            )
        ).one()
    for check, ok, checked_at in checks:
        OPS_CHECK_OK.labels(check=check).set(1 if ok else 0)
        OPS_CHECK_TIMESTAMP.labels(check=check).set(checked_at.timestamp())
    for repo, kind, finished_at in runs:
        BACKUP_LAST_SUCCESS.labels(repo=str(repo), type=kind).set(finished_at.timestamp())
    if archiver.last_archived_time is not None:
        WAL_LAST_ARCHIVED.set(archiver.last_archived_time.timestamp())
    WAL_ARCHIVE_FAILED.set(archiver.failed_count)
