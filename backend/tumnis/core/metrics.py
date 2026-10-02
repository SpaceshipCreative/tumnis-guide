"""Prometheus registry and the /metrics route (P0-27, FR-12.3, REL-5).

`GET /metrics` sits at the root (not under /v1) behind a bearer token from
METRICS_TOKEN_FILE, compared in constant time; without a configured token it answers 401
to everyone (prod refuses to start without one). Each scrape refreshes the gauges that
live in the database first, one query per source and no per-workspace loop, as the app
role: DBOS system tables sit outside row-level security, and the two tenant tables read
here (`dead_letters`, `usage_counters`) are summed by SECURITY DEFINER functions. No
metric carries a workspace label, so hosted mode needs no change.

- P0-27: `tumnis_http_request_duration_seconds` (ASGI middleware; `route` is the matched
  route template, so cardinality stays bounded), `tumnis_queue_depth`, `tumnis_workflows`
  (last 24 h), `tumnis_dead_letters`, `tumnis_usage_total` (P0-21's counters, summed).
- P0-08: the cache hit and miss counters, labelled with the registered cache name.
- P0-28: the backup and operations gauges (`ops_status`, `ops_backup_runs`,
  `pg_stat_archiver`); the audit chain reaches the api the same way, as ops_status check
  `audit_chain` written by the worker's nightly verify (P0-15; its per-workspace gauges
  stay in the worker process, which serves no /metrics).
"""

import hmac
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any, Final

from fastapi import APIRouter, HTTPException, Request
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client.metrics_core import GaugeMetricFamily, Metric
from prometheus_client.registry import Collector
from psycopg import errors as pg_errors
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.responses import Response
from starlette.routing import Mount
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tumnis.core import db

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


# --- P0-27: requests, queues, workflows, dead letters, usage --------------------------------

# DBOS 3.1.0 WorkflowStatusString and the dead_letters statuses (core_0004): each reported,
# at 0 when absent, so an alert on a status never sees a missing series.
WORKFLOW_STATUSES: Final = (
    "PENDING",
    "ENQUEUED",
    "DELAYED",
    "SUCCESS",
    "ERROR",
    "CANCELLED",
    "MAX_RECOVERY_ATTEMPTS_EXCEEDED",
)
DEAD_LETTER_STATUSES: Final = ("open", "retrying", "resolved", "discarded")
WORKFLOW_WINDOW_S: Final = 86400
HTTP_METHODS: Final = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})

REQUEST_DURATION = Histogram(
    "tumnis_http_request_duration_seconds",
    "HTTP request duration by route template, method and status",
    ["route", "method", "status"],
    registry=REGISTRY,
)


class SnapshotGauge(Collector):
    """A one-label gauge replaced whole at each scrape, so a label that disappears from the
    source disappears from the exposition (a labelled Gauge would keep it forever)."""

    def __init__(self, name: str, documentation: str, label: str) -> None:
        self.name, self.documentation, self.label = name, documentation, label
        self._values: dict[str, float] = {}
        REGISTRY.register(self)

    def set_all(self, values: Mapping[str, float]) -> None:
        self._values = dict(values)

    def _family(self) -> GaugeMetricFamily:
        return GaugeMetricFamily(self.name, self.documentation, labels=[self.label])

    def describe(self) -> Iterable[Metric]:
        return [self._family()]

    def collect(self) -> Iterable[Metric]:
        family = self._family()
        for value, amount in sorted(self._values.items()):
            family.add_metric([value], amount)
        return [family]


QUEUE_DEPTH = SnapshotGauge(
    "tumnis_queue_depth", "Workflows waiting on each DBOS queue (status ENQUEUED)", "queue"
)
WORKFLOWS = SnapshotGauge(
    "tumnis_workflows", "DBOS workflows created in the last 24 hours, by status", "status"
)
DEAD_LETTERS = SnapshotGauge(
    "tumnis_dead_letters", "Dead-lettered deliveries across workspaces, by status", "status"
)
USAGE_TOTAL = SnapshotGauge(
    "tumnis_usage_total", "Usage counters summed over every day and workspace", "counter"
)
CONNECTOR_SYNC_AGE = SnapshotGauge(
    "tumnis_connector_sync_age_seconds",
    "Seconds since the least recently synced live connection of each provider last synced"
    " well (or was made), P3-02",
    "provider",
)
CONNECTOR_ITEMS = SnapshotGauge(
    "tumnis_connector_items_total",
    "Items the syncs of each provider's live connections have read, P3-02",
    "provider",
)

_QUEUE_DEPTH_SQL = text(
    """
    SELECT name, sum(n)::bigint FROM (
      SELECT queue_name AS name, count(*) AS n FROM dbos.workflow_status
       WHERE status = 'ENQUEUED' AND queue_name IS NOT NULL GROUP BY queue_name
      UNION ALL
      SELECT name, 0 FROM dbos.queues
    ) AS q GROUP BY name
    """
)
_WORKFLOWS_SQL = text(
    "SELECT status, count(*) FROM dbos.workflow_status"
    " WHERE created_at > (extract(epoch FROM now()) - :window) * 1000 GROUP BY status"
)
# A fresh deployment whose worker has not launched DBOS yet has no dbos schema or tables.
_NO_DBOS_SCHEMA: Final = (pg_errors.InvalidSchemaName, pg_errors.UndefinedTable)
_DEAD_LETTERS_SQL = text("SELECT status, n FROM app.dead_letter_counts()")

# Extra sources a composition root adds (tumnis.wiring: usage totals from the usage module,
# which core cannot import). Each reads through the app-role connection it is given.
ScrapeSource = Callable[[AsyncConnection], Awaitable[None]]
_SOURCES: dict[str, ScrapeSource] = {}


def register_scrape_source(name: str, source: ScrapeSource) -> None:
    _SOURCES[name] = source


def _with_zeros(rows: Iterable[Any], names: Iterable[str]) -> dict[str, float]:
    values = dict.fromkeys(names, 0.0)
    values.update({str(name): float(n) for name, n in rows})
    return values


async def refresh_scrape_gauges(engine: AsyncEngine, dbos_engine: AsyncEngine) -> None:
    """Every database-backed gauge: ops and backups, dead letters and the registered
    sources on the app database; queue depth and workflows on the DBOS system database."""
    await refresh_ops_gauges(engine)
    async with engine.connect() as conn:
        dead = (await conn.execute(_DEAD_LETTERS_SQL)).all()
        for source in _SOURCES.values():
            await source(conn)
    DEAD_LETTERS.set_all(_with_zeros(dead, DEAD_LETTER_STATUSES))
    try:
        async with dbos_engine.connect() as conn:
            depth = (await conn.execute(_QUEUE_DEPTH_SQL)).all()
            statuses = (await conn.execute(_WORKFLOWS_SQL, {"window": WORKFLOW_WINDOW_S})).all()
    except ProgrammingError as exc:
        if not isinstance(exc.orig, _NO_DBOS_SCHEMA):
            raise
        depth, statuses = [], []  # the worker has not created the DBOS schema yet
    QUEUE_DEPTH.set_all(_with_zeros(depth, ()))
    WORKFLOWS.set_all(_with_zeros(statuses, WORKFLOW_STATUSES))


# --- The route and the request middleware ---------------------------------------------------

_dbos_url: str | None = None
_dbos_engine: AsyncEngine | None = None


def configure(dbos_system_url: str) -> None:
    """Where the DBOS system tables are (create_app; no I/O until the first scrape)."""
    global _dbos_url, _dbos_engine  # process-wide, like tumnis.core.db
    _dbos_url, _dbos_engine = dbos_system_url, None


def dbos_engine() -> AsyncEngine:
    """NullPool: a scrape every few seconds needs no pool, and a dropped database shows."""
    global _dbos_engine  # noqa: PLW0603
    if _dbos_url is None:
        raise RuntimeError("tumnis.core.metrics.configure() was not called")
    if _dbos_engine is None:
        _dbos_engine = create_async_engine(_dbos_url, poolclass=NullPool)
    return _dbos_engine


async def dispose() -> None:
    global _dbos_engine  # noqa: PLW0603
    if _dbos_engine is not None:
        await _dbos_engine.dispose()
        _dbos_engine = None


def require_bearer(request: Request, token: str | None) -> None:
    """401 unless the request carries `Authorization: Bearer <token>` (constant time)."""
    scheme, _, given = request.headers.get("authorization", "").partition(" ")
    if (
        token is None
        or scheme.lower() != "bearer"
        or not hmac.compare_digest(given.strip().encode(), token.encode())
    ):
        raise HTTPException(
            status_code=401, detail="metrics_unauthorized", headers={"WWW-Authenticate": "Bearer"}
        )


router = APIRouter(tags=["ops"])


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    require_bearer(request, request.app.state.metrics_token)
    await refresh_scrape_gauges(db.app_engine(), dbos_engine())
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


def route_label(scope: Scope) -> str:
    """The matched route's template (`/v1/projects/{project_id}`), never the raw path."""
    route = scope.get("route")
    if isinstance(route, Mount):
        return "<static>"
    path = getattr(route, "path", None)
    # A route behind include_router (every /v1 route, P0-10) carries its own path only;
    # FastAPI 0.141 keeps the full template on the effective route context.
    context = scope.get("fastapi", {}).get("effective_route_context")
    if context is not None and getattr(context, "original_route", None) is route:
        path = getattr(context, "path", path)
    return path if isinstance(path, str) and path else "<unmatched>"


class RequestMetricsMiddleware:
    """Observes tumnis_http_request_duration_seconds for every HTTP request (pure ASGI, so
    the router's `scope["route"]` is visible once the app returns)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        status = 500
        start = time.perf_counter()

        async def send_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_status)
        finally:
            method = scope["method"] if scope["method"] in HTTP_METHODS else "OTHER"
            REQUEST_DURATION.labels(
                route=route_label(scope), method=method, status=str(status)
            ).observe(time.perf_counter() - start)
