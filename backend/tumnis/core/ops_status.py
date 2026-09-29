"""ops_status: the latest result of each operations check (P0-28).

The worker's checks (`backups`, later `audit_chain`) and `tumnis drill record`
(`restore_drill`) upsert one row per check; readiness and the ops gauges read it.
"""

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from tumnis.core.health import HealthCheck, Status

_UPSERT = text(
    """
    INSERT INTO ops_status ("check", ok, checked_at, details)
    VALUES (:check, :ok, :checked_at, CAST(:details AS jsonb))
    ON CONFLICT ("check") DO UPDATE
       SET ok = excluded.ok, checked_at = excluded.checked_at, details = excluded.details
    """
)


async def record(
    conn: AsyncConnection,
    check: str,
    *,
    ok: bool,
    checked_at: datetime,
    details: Mapping[str, Any],
) -> None:
    """Upsert the check's row in the caller's transaction."""
    await conn.execute(
        _UPSERT,
        {"check": check, "ok": ok, "checked_at": checked_at, "details": json.dumps(details)},
    )


def health_check(engine: Callable[[], AsyncEngine], check: str) -> HealthCheck:
    """Readiness for one ops check: degraded while its last result is a failure. No row
    yet (the check has not run on this deployment) is ok."""

    async def run() -> Status:
        async with engine().connect() as conn:
            ok = (
                await conn.execute(
                    text('SELECT ok FROM ops_status WHERE "check" = :check'), {"check": check}
                )
            ).scalar_one_or_none()
        return "degraded" if ok is False else "ok"

    return run
