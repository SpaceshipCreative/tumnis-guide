"""calendar public functions and DTOs; the only file other modules may import.

Calendar owns the canonical `events` table (P0-12, R-15). A calendar connector maps its
provider's events to `EventRecord`s and stores them with `upsert_events`, which runs the
shared canonical upsert on this module's table; raw payloads go through
`integrations.api.store_raw_payloads`.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field
from sqlalchemy import Table, select
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.canonical import CanonicalRecord, UpsertStats, upsert_records
from tumnis.core.schemas import versioned
from tumnis.core.tenancy import WorkspaceContext, session_for
from tumnis.modules.calendar.models import Event
from tumnis.modules.integrations import api as integrations

_events: Table = Event.__table__  # type: ignore[assignment]


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
    """Upserts the connection's events on (workspace, connection, external id); `raw_ids`
    maps an event's external id to its `raw_payloads` row (from
    `integrations.api.store_raw_payloads`)."""
    async with session_for(ctx, session) as s:
        source = await integrations.connection_source(s, connection_id)
        return await upsert_records(
            s, _events, connection_id, records, raw_ids or {}, _columns, source=source
        )


def _columns(rec: EventRecord) -> dict[str, Any]:
    return {
        "title": rec.title,
        "start_at": rec.start_at,
        "end_at": rec.end_at,
        "all_day": rec.all_day,
        "attendees": rec.attendees,
        "busy": rec.busy,
    }


async def _event_taint(session: AsyncSession, event_id: UUID) -> bool | None:
    tainted: bool | None = await session.scalar(
        select(_events.c.tainted).where(_events.c.id == event_id)
    )
    return tainted


integrations.register_target_taint("event", _event_taint)
