"""calendar public functions and DTOs; the only file other modules may import.

Calendar owns the canonical `events` table (P0-12, R-15). A calendar connector maps its
provider's events to `EventRecord`s and stores them with `upsert_events`, which runs the
shared canonical upsert on this module's table; raw payloads go through
`integrations.api.store_raw_payloads`.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import Table, select
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.adapters.registry import Health
from tumnis.core.canonical import CanonicalRecord, UpsertStats, upsert_records
from tumnis.core.clock import Clock
from tumnis.core.schemas import versioned
from tumnis.core.tenancy import WorkspaceContext, session_for, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.calendar.adapters.port import CalendarInfo, GoogleCalendarPort, TokenSet
from tumnis.modules.calendar.models import Event
from tumnis.modules.calendar.rules import AccountStatus
from tumnis.modules.integrations import api as integrations
from tumnis.modules.integrations.api import (
    Capability,
    ConnectorKind,
    RawItem,
    SyncPage,
    register_connector,
)
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


# --- Google Calendar (P1-09) ------------------------------------------------------------------
# Interface stubs: the spec tests (T-P1-09-01 to 12) name these; P1-09's implementation
# fills them in.

PROVIDER = "google_calendar"
OAUTH_SECTION = "calendar.google"


class GoogleOAuthClient(BaseModel):
    """Settings > Calendar: the workspace's own Google Cloud OAuth client."""

    client_id: str = ""
    client_secret: str = ""


class CalendarCursor(BaseModel):
    calendar_index: int
    window_start: AwareDatetime
    window_end: AwareDatetime
    page_token: str | None = None


class EventOut(BaseModel):
    id: UUID
    connection_id: UUID
    calendar_id: str | None
    external_id: str
    title: str | None
    start_at: datetime
    end_at: datetime
    all_day: bool
    busy: bool
    provider_url: str | None


class CalendarOut(BaseModel):
    id: str
    summary: str
    primary: bool = False
    time_zone: str | None = None


class CalendarAccountOut(BaseModel):
    id: UUID
    connection_id: UUID
    google_email: str
    status: AccountStatus
    calendars: list[CalendarOut]
    selected_calendar_ids: list[str]
    last_sync_at: datetime | None
    version: int


def consent_url(*, client_id: str, redirect_uri: str, state: str, code_challenge: str) -> str:
    """Google's consent URL: the read-only scopes, offline access, forced consent, PKCE."""
    raise NotImplementedError


class GoogleCalendar:
    """The Google Calendar connector (P0-12 protocol)."""

    kind: ConnectorKind = "calendar"
    provider: str = PROVIDER
    capabilities: frozenset[Capability] = frozenset({"poll", "read"})

    def __init__(
        self,
        *,
        api: GoogleCalendarPort | None = None,
        access_token: str = "",
        calendar_ids: Sequence[str] = (),
        self_email: str = "",
        window: tuple[datetime, datetime] | None = None,
        clock: Clock | None = None,
        status: AccountStatus = "connected",
    ) -> None:
        self._api = api

    async def sync(self, cursor: dict[str, Any] | None) -> SyncPage:
        raise NotImplementedError

    def map(self, raw: RawItem) -> list[CanonicalRecord]:
        raise NotImplementedError

    async def health(self) -> Health:
        raise NotImplementedError


async def ingest_events_page(
    ctx: WorkspaceContext,
    connection_id: UUID,
    connector: GoogleCalendar,
    page: SyncPage,
    *,
    session: AsyncSession | None = None,
) -> UpsertStats:
    raise NotImplementedError


async def events_between(
    ctx: WorkspaceContext, start: datetime, end: datetime, *, session: AsyncSession | None = None
) -> list[EventOut]:
    raise NotImplementedError


async def list_accounts(
    ctx: WorkspaceContext, *, session: AsyncSession | None = None
) -> list[CalendarAccountOut]:
    raise NotImplementedError


async def connect_account(
    ctx: WorkspaceContext,
    tokens: TokenSet,
    calendars: Sequence[CalendarInfo],
    *,
    session: AsyncSession | None = None,
) -> CalendarAccountOut:
    raise NotImplementedError


async def build_connector(
    ctx: WorkspaceContext,
    connection_id: UUID,
    *,
    api: GoogleCalendarPort,
    clock: Clock | None = None,
    session: AsyncSession | None = None,
) -> GoogleCalendar:
    raise NotImplementedError


def _real_connector(**deps: Any) -> GoogleCalendar:
    return GoogleCalendar(**deps)


def _fake_connector(**deps: Any) -> GoogleCalendar:
    return GoogleCalendar(**deps)


register_connector(PROVIDER, "calendar", real=_real_connector, fake=_fake_connector)
