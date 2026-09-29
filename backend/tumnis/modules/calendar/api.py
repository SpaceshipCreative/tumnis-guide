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
from tumnis.core.tenancy import WorkspaceContext, session_for, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.calendar.models import Event
from tumnis.modules.integrations import api as integrations
from tumnis.seed import EventSeed, register_seed_writer

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


# --- Seed writer (P0-02's seed sets) ----------------------------------------------------------

SEED_PROVIDER = "seed"


async def seed_event(workspace_id: UUID, rec: EventSeed) -> UUID:
    """A seed event as the system actor, on the workspace's seed calendar connection
    (created on first use); the seed key is its external id, so a reload updates it."""
    record = EventRecord(
        external_id=rec.key,
        fetched_at=rec.fetched_at or rec.start_at,
        title=rec.title,
        start_at=rec.start_at,
        end_at=rec.end_at,
        busy=rec.busy,
    )
    ctx = WorkspaceContext(workspace_id, SYSTEM_ACTOR)
    async with tenant_session(ctx) as s:
        connection_id = await integrations.seed_connection(
            s, "calendar", SEED_PROVIDER, SEED_PROVIDER
        )
        await upsert_events(ctx, connection_id, [record], session=s)
        event_id: UUID = (
            await s.execute(
                select(_events.c.id).where(
                    _events.c.connection_id == connection_id, _events.c.external_id == rec.key
                )
            )
        ).scalar_one()
    return event_id


register_seed_writer("event", seed_event)
