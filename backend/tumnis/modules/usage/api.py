"""usage public functions and DTOs; the only file other modules may import.

Counting is once per (event, counter): `record` writes the ledger row and raises the day's
counter in one statement, and a ledger row that already exists (a second delivery of the
same event) adds nothing. DBOS's delivery IDs stop most doubles first; the ledger covers
the rest (a manual dead-letter retry after a success, a relay replay after the
deduplication window).
"""

from datetime import date
from typing import Final

from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from tumnis.core.events import EventEnvelope
from tumnis.core.metrics import USAGE_TOTAL
from tumnis.modules.usage.models import UsageCounter
from tumnis.modules.usage.rules import increments, usage_day


class UsageRow(BaseModel):
    day: date
    counter: str
    value: int


_RECORD: Final = text(
    """
    WITH ins AS (
      INSERT INTO usage_ledger (workspace_id, event_id, counter, day, amount)
      VALUES (:ws, :event_id, :counter, :day, :amount)
      ON CONFLICT (workspace_id, event_id, counter) DO NOTHING
      RETURNING day, counter, amount)
    INSERT INTO usage_counters (workspace_id, day, counter, value)
    SELECT :ws, day, counter, amount FROM ins
    ON CONFLICT (workspace_id, day, counter)
    DO UPDATE SET value = usage_counters.value + EXCLUDED.value,
                  version = usage_counters.version + 1
    RETURNING 1
    """
)


async def record(s: AsyncSession, env: EventEnvelope) -> int:
    """Count `env` in its workspace, in the caller's transaction (which must be in that
    workspace's context: row-level security refuses any other). Returns the counters
    raised: 0 for an event the map does not count, and 0 when it was counted before."""
    day = usage_day(env.occurred_at)
    raised = 0
    for counter, amount in increments(env.name, env.payload):
        params = {
            "ws": env.workspace_id,
            "event_id": env.event_id,
            "counter": counter,
            "day": day,
            "amount": amount,
        }
        raised += len((await s.execute(_RECORD, params)).all())
    return raised


async def report(s: AsyncSession, day_from: date, day_to: date) -> list[UsageRow]:
    """The workspace's counters from `day_from` to `day_to` (both inclusive, UTC days),
    ordered by day then counter."""
    rows = await s.execute(
        select(UsageCounter.day, UsageCounter.counter, UsageCounter.value)
        .where(
            UsageCounter.day.between(day_from, day_to),
            UsageCounter.deleted_at.is_(None),
        )
        .order_by(UsageCounter.day, UsageCounter.counter)
    )
    return [UsageRow(day=day, counter=counter, value=value) for day, counter, value in rows]


_TOTALS: Final = text("SELECT counter, total FROM app.usage_totals()")


async def export_metrics(conn: AsyncConnection) -> None:
    """tumnis_usage_total{counter}: every counter summed over all days and workspaces, read
    through the SECURITY DEFINER app.usage_totals() (the scrape has no workspace context).
    tumnis.wiring registers it as a /metrics scrape source (P0-27)."""
    rows = (await conn.execute(_TOTALS)).all()
    USAGE_TOTAL.set_all({counter: float(total) for counter, total in rows})
