"""calendar public functions and DTOs; the only file other modules may import.

Calendar owns the canonical `events` table (P0-12, R-15). A calendar connector maps its
provider's events to `EventRecord`s and stores them with `upsert_events`, which runs the
shared canonical upsert on this module's table; raw payloads go through
`integrations.api.store_raw_payloads`.
"""

from collections.abc import Mapping, Sequence
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.canonical import CanonicalRecord, UpsertStats
from tumnis.core.schemas import versioned
from tumnis.core.tenancy import WorkspaceContext


@versioned("entities", "event", 1)
class EventRecord(CanonicalRecord):
    schema_version: Literal[1] = 1
    record_type: Literal["event"] = "event"
    title: str | None = None
    start_at: AwareDatetime
    end_at: AwareDatetime
    all_day: bool = False
    attendees: list[str] = Field(default_factory=list)
    busy: bool = True


async def upsert_events(
    ctx: WorkspaceContext,
    connection_id: UUID,
    records: Sequence[EventRecord],
    *,
    raw_ids: Mapping[str, UUID] | None = None,
    session: AsyncSession | None = None,
) -> UpsertStats:
    """Upserts the connection's events on (workspace, connection, external id)."""
    raise NotImplementedError
