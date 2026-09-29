"""usage public functions and DTOs; the only file other modules may import."""

from datetime import date

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.events import EventEnvelope


class UsageRow(BaseModel):
    day: date
    counter: str
    value: int


async def record(s: AsyncSession, env: EventEnvelope) -> int:
    raise NotImplementedError("P0-21")


async def report(s: AsyncSession, day_from: date, day_to: date) -> list[UsageRow]:
    raise NotImplementedError("P0-21")
