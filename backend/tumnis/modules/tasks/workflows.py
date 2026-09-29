"""tasks DBOS workflows (P0-19): the day-close tick, day close, the recurrence tick and
housekeeping.

Interfaces only until the P0-19 spec tests turn green.
"""

from datetime import date, datetime
from typing import Any, Final
from uuid import UUID

HOUSEKEEPING_BATCH: Final = 1_000  # plan default: rows deleted per batch


async def day_close_tick(scheduled_time: datetime, context: Any) -> None:
    raise NotImplementedError


async def close_day(workspace_id: UUID, day: date, now: datetime | None = None) -> int:
    raise NotImplementedError


async def recurrence_tick(workspace_id: UUID, now: datetime | None = None) -> int:
    raise NotImplementedError


async def housekeeping(scheduled_time: datetime, context: Any) -> None:
    raise NotImplementedError
