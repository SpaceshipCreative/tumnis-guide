"""usage public functions and DTOs; the only file other modules may import.

Counting is once per (event, counter): `record` writes the ledger row and raises the day's
counter in one statement, and a ledger row that already exists (a second delivery of the
same event) adds nothing. DBOS's delivery IDs stop most doubles first; the ledger covers
the rest (a manual dead-letter retry after a success, a relay replay after the
deduplication window).
"""

from datetime import date
from typing import Final
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from tumnis.core.events import EventEnvelope
from tumnis.core.metrics import USAGE_TOTAL
from tumnis.modules.usage.models import UsageCounter

# The local success metrics (P1-18): planning gathers their inputs and calls them here.
from tumnis.modules.usage.rules import PlanDayFacts as PlanDayFacts  # noqa: PLC0414
from tumnis.modules.usage.rules import PlannedOutcome as PlannedOutcome  # noqa: PLC0414
from tumnis.modules.usage.rules import (
    consecutive_plan_days as consecutive_plan_days,  # noqa: PLC0414
)
from tumnis.modules.usage.rules import daily_open_rate as daily_open_rate  # noqa: PLC0414
from tumnis.modules.usage.rules import estimate_error as estimate_error  # noqa: PLC0414
from tumnis.modules.usage.rules import increments, usage_day
from tumnis.modules.usage.rules import rollover_rate as rollover_rate  # noqa: PLC0414
from tumnis.modules.usage.rules import (
    tasks_completed_per_working_day as tasks_completed_per_working_day,  # noqa: PLC0414
)


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


# --- App opens (P1-18) ---------------------------------------------------------------------------

APP_OPEN: Final = "app_open"
_OPEN: Final = text(
    """
    INSERT INTO usage_counters (workspace_id, day, counter, value)
    VALUES (:ws, :day, :counter, 1)
    ON CONFLICT (workspace_id, day, counter)
    DO UPDATE SET value = usage_counters.value + 1, version = usage_counters.version + 1
    """
)


async def record_open(s: AsyncSession, workspace_id: UUID, day: date) -> None:
    """Counts one app open on `day`, in the caller's transaction. Unlike the event counters
    (UTC days), `day` is the workspace's local day, which the caller works out (usage reads
    no other module): the daily open rate counts local days (P1-18)."""
    await s.execute(_OPEN, {"ws": workspace_id, "day": day, "counter": APP_OPEN})


async def open_days(s: AsyncSession, day_from: date, day_to: date) -> set[date]:
    """The local days from `day_from` to `day_to` (both inclusive) the app was opened on, for
    the workspace in context."""
    rows = await s.execute(
        select(UsageCounter.day).where(
            UsageCounter.counter == APP_OPEN,
            UsageCounter.day.between(day_from, day_to),
            UsageCounter.value > 0,
            UsageCounter.deleted_at.is_(None),
        )
    )
    return {day for (day,) in rows}


_TOTALS: Final = text("SELECT counter, total FROM app.usage_totals()")


async def export_metrics(conn: AsyncConnection) -> None:
    """tumnis_usage_total{counter}: every counter summed over all days and workspaces, read
    through the SECURITY DEFINER app.usage_totals() (the scrape has no workspace context).
    tumnis.wiring registers it as a /metrics scrape source (P0-27)."""
    rows = (await conn.execute(_TOTALS)).all()
    USAGE_TOTAL.set_all({counter: float(total) for counter, total in rows})
