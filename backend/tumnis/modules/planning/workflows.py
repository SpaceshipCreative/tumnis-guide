"""planning DBOS workflows and steps."""

from datetime import date, datetime
from typing import Any
from uuid import UUID


async def planner_tick(scheduled_time: datetime, context: Any) -> None:
    raise NotImplementedError


async def build_plan(workspace_id: UUID, day: date, trigger: str, now: datetime) -> UUID:
    raise NotImplementedError
