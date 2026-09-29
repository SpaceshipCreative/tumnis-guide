"""emit(): one outbox row and a NOTIFY in the caller's transaction (P0-07, ADR-0011)."""

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.events import EventPayload


class EventSchemaError(ValueError):
    """The payload's (name, version) is not registered, or it fails its model."""


async def emit(session: AsyncSession, payload: EventPayload, *, occurred_at: datetime) -> UUID:
    raise NotImplementedError("P0-07")
