"""Health registry and the /health/live and /health/ready routes (P0-04, REL-5).

Liveness does no I/O at all. Readiness runs every registered check concurrently, each with
a 1 s timeout (plan default). A critical check that fails or times out makes readiness 503
`down`; a non-critical one (a module's `api.health()`) only marks itself `degraded`, so one
failing module degrades the app instead of taking it down. The routes sit at the root, not
under /v1: they are operational endpoints for Coolify and Prometheus.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

Status = Literal["ok", "degraded", "down"]
HealthCheck = Callable[[], Awaitable[Status]]

CHECK_TIMEOUT_S = 1.0  # plan default
_CHECKS: dict[str, HealthCheck] = {}  # "postgres", "dbos", "module:<name>"
_CRITICAL: set[str] = set()
_log = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


def register_health(name: str, check: HealthCheck, *, critical: bool) -> None:
    _CHECKS[name] = check
    if critical:
        _CRITICAL.add(name)
    else:
        _CRITICAL.discard(name)


def clear_health() -> None:
    """Forget every check; `create_app` starts from an empty registry."""
    _CHECKS.clear()
    _CRITICAL.clear()


async def _run(name: str, check: HealthCheck) -> Status:
    try:
        status = await asyncio.wait_for(check(), CHECK_TIMEOUT_S)
    except Exception:  # a failing check is a result, not an error
        _log.warning("health check %s failed", name, exc_info=True)
        status = "down"
    if status == "down" and name not in _CRITICAL:
        return "degraded"
    return status


async def readiness() -> tuple[Status, dict[str, Status]]:
    names = list(_CHECKS)
    results = await asyncio.gather(*(_run(name, _CHECKS[name]) for name in names))
    checks = dict(zip(names, results, strict=True))
    if any(checks[name] == "down" for name in _CRITICAL if name in checks):
        return "down", checks
    if any(status != "ok" for status in checks.values()):
        return "degraded", checks
    return "ok", checks


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/health/ready",
    responses={
        200: {"description": "Ready (`ok`) or `degraded`: a non-critical check failed"},
        503: {"description": "Down: a critical check failed"},
    },
)
async def ready() -> JSONResponse:
    status, checks = await readiness()
    return JSONResponse(
        {"status": status, "checks": checks}, status_code=503 if status == "down" else 200
    )


# --- Core checks ----------------------------------------------------------------------------


def sql_check(engine: Callable[[], AsyncEngine], statement: str) -> HealthCheck:
    """ok when `statement` runs on a connection from `engine()`."""

    async def check() -> Status:
        async with engine().connect() as conn:
            await conn.execute(text(statement))
        return "ok"

    return check


def dbos_check(system_database_url: str) -> HealthCheck:
    """ok when the DBOS system tables answer (table name checked against DBOS 3.1.0).
    NullPool: every probe opens a fresh connection, so a dropped database shows at once."""
    engine = create_async_engine(system_database_url, poolclass=NullPool)
    return sql_check(lambda: engine, "SELECT 1 FROM dbos.workflow_status LIMIT 1")
