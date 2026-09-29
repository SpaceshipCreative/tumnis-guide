"""calendar public functions and DTOs; the only file other modules may import.

Calendar owns the canonical `events` table (P0-12, R-15). A calendar connector maps its
provider's events to `EventRecord`s and stores them with `upsert_events`, which runs the
shared canonical upsert on this module's table; raw payloads go through
`integrations.api.store_raw_payloads`.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, tzinfo
from typing import Any, Literal
from urllib.parse import urlencode
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, Field, SecretStr
from sqlalchemy import Table, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import deadletter
from tumnis.core.adapters.registry import Health
from tumnis.core.canonical import (
    CanonicalRecord,
    UpsertStats,
    soft_delete_records,
    upsert_records,
)
from tumnis.core.clock import Clock, SystemClock
from tumnis.core.outbox import emit
from tumnis.core.schemas import versioned
from tumnis.core.settings_store import SettingSection, get_setting, register_section
from tumnis.core.tenancy import WorkspaceContext, session_for, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.core.versioning import NotFound, update_versioned
from tumnis.modules.auth import api as auth
from tumnis.modules.calendar.adapters.fake import FakeGoogleCalendar
from tumnis.modules.calendar.adapters.port import (
    CalendarInfo,
    GoogleCalendarPort,
    OAuthClient,
    TokenSet,
)
from tumnis.modules.calendar.models import CalendarAccount, Event
from tumnis.modules.calendar.payloads import CalendarSyncedV1, SyncWindow
from tumnis.modules.calendar.rules import (
    READONLY_SCOPES,
    AccountStatus,
    Tombstone,
    map_event,
    sync_window,
)
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
    calendar_id: str | None = None
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
        "calendar_id": rec.calendar_id,
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
SYNC_QUEUE = "sync"  # A9
SYNC_WORKFLOW = "calendar_connector_sync"
EXCHANGE_WORKFLOW = "calendar_oauth_exchange"
CALLBACK_PATH = "/v1/calendar/oauth/callback"


class GoogleOAuthClient(BaseModel):
    """Settings > Calendar: the workspace's own Google Cloud OAuth client."""

    client_id: str = ""
    client_secret: str = ""


register_section(
    SettingSection(OAUTH_SECTION, GoogleOAuthClient, secret_fields=frozenset({"client_secret"}))
)


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


CONSENT_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"


def consent_url(*, client_id: str, redirect_uri: str, state: str, code_challenge: str) -> str:
    """Google's consent URL: the read-only scopes, offline access, forced consent, PKCE."""
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(sorted(READONLY_SCOPES)),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{CONSENT_ENDPOINT}?{query}"


class GoogleCalendar:
    """The Google Calendar connector (P0-12 protocol): pages through events.list of each
    selected calendar over a fixed window (a `CalendarCursor` says where it is), hands out
    one `RawItem` per event and the cancelled ones as `deleted`; `map` is `rules.map_event`.
    """

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
        self._access_token = access_token
        self._calendar_ids = list(calendar_ids)
        self._self_email = self_email
        self._clock = clock or SystemClock()
        self._window = window
        self.status = status

    @property
    def calendar_ids(self) -> list[str]:
        return list(self._calendar_ids)

    def first_cursor(self) -> CalendarCursor:
        """Where a sync with no stored cursor starts: the first calendar, the window."""
        start, end = self._window or sync_window(self._clock.now().date(), ZoneInfo("UTC"))
        return CalendarCursor(calendar_index=0, window_start=start, window_end=end)

    async def sync(self, cursor: dict[str, Any] | None) -> SyncPage:
        at = CalendarCursor.model_validate(cursor) if cursor else self.first_cursor()
        if self._api is None or at.calendar_index >= len(self._calendar_ids):
            return SyncPage(items=[], next_cursor=None, has_more=False)
        calendar_id = self._calendar_ids[at.calendar_index]
        response = await self._api.list_events(
            self._access_token,
            calendar_id,
            time_min=at.window_start,
            time_max=at.window_end,
            page_token=at.page_token,
        )
        fetched_at = self._clock.now()
        time_zone = response.get("timeZone") or "UTC"
        items: list[RawItem] = []
        deleted: list[str] = []
        for event in response.get("items", []):
            external_id = f"{calendar_id}:{event['id']}"
            if event.get("status") == "cancelled":
                deleted.append(external_id)
                continue
            payload = {
                "calendar_id": calendar_id,
                "self_email": self._self_email,
                "time_zone": time_zone,
                "event": event,
            }
            items.append(
                RawItem(
                    external_id=external_id,
                    record_type="event",
                    payload=payload,
                    fetched_at=fetched_at,
                )
            )
        next_at = _next_cursor(at, response.get("nextPageToken"), len(self._calendar_ids))
        return SyncPage(
            items=items,
            deleted=deleted,
            next_cursor=None if next_at is None else next_at.model_dump(mode="json"),
            has_more=next_at is not None,
        )

    def map(self, raw: RawItem) -> list[CanonicalRecord]:
        payload = raw.payload
        mapped = map_event(
            payload["event"],
            calendar_id=payload["calendar_id"],
            self_email=payload.get("self_email", ""),
            calendar_tz=_zone(payload.get("time_zone")),
        )
        if isinstance(mapped, Tombstone):
            return []
        return [EventRecord(**mapped.model_dump(), fetched_at=raw.fetched_at)]

    async def health(self) -> Health:
        if self.status == "needs_reauth":
            return "degraded"
        state = getattr(self._api, "health_state", None)
        health: Health = state() if callable(state) else "ok"
        return health


def _next_cursor(
    at: CalendarCursor, page_token: str | None, calendars: int
) -> CalendarCursor | None:
    """The same calendar's next page, else the next calendar's first, else None (done)."""
    if page_token:
        return at.model_copy(update={"page_token": page_token})
    if at.calendar_index + 1 < calendars:
        return at.model_copy(update={"calendar_index": at.calendar_index + 1, "page_token": None})
    return None


def _zone(name: str | None) -> tzinfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


async def ingest_events_page(
    ctx: WorkspaceContext,
    connection_id: UUID,
    connector: GoogleCalendar,
    page: SyncPage,
    *,
    session: AsyncSession | None = None,
) -> UpsertStats:
    """One sync page into `events`: raw payloads stored, items mapped and upserted, the
    cancelled ones soft-deleted. Idempotent: a page delivered again changes nothing."""
    async with session_for(ctx, session) as s:
        raw_ids = await integrations.store_raw_payloads(ctx, connection_id, page.items, session=s)
        records: list[EventRecord] = []
        record_raw: dict[str, UUID] = {}
        for item in page.items:
            for rec in connector.map(item):
                if isinstance(rec, EventRecord):
                    records.append(rec)
                    record_raw[rec.external_id] = raw_ids[(item.record_type, item.external_id)]
        stats = await upsert_events(ctx, connection_id, records, raw_ids=record_raw, session=s)
        await soft_delete_records(s, _events, connection_id, page.deleted)
    return stats


async def events_between(
    ctx: WorkspaceContext, start: datetime, end: datetime, *, session: AsyncSession | None = None
) -> list[EventOut]:
    """The workspace's live events overlapping [start, end) from every connection (every
    Google account and the seed calendar), by start time; each carries its busy flag."""
    async with session_for(ctx, session) as s:
        rows = await s.execute(
            select(_events)
            .where(
                _events.c.deleted_at.is_(None),
                _events.c.start_at < end,
                _events.c.end_at > start,
            )
            .order_by(_events.c.start_at, _events.c.id)
        )
        return [EventOut.model_validate(row._mapping) for row in rows]


_accounts: Table = CalendarAccount.__table__  # type: ignore[assignment]


def _account_out(row: Any) -> CalendarAccountOut:
    return CalendarAccountOut.model_validate(dict(row))


async def list_accounts(
    ctx: WorkspaceContext, *, session: AsyncSession | None = None
) -> list[CalendarAccountOut]:
    """Every connected Google account of the workspace, oldest first."""
    async with session_for(ctx, session) as s:
        rows = await s.execute(
            select(_accounts)
            .where(_accounts.c.deleted_at.is_(None))
            .order_by(_accounts.c.created_at, _accounts.c.id)
        )
        return [_account_out(row) for row in rows.mappings()]


async def get_account(
    ctx: WorkspaceContext, account_id: UUID, *, session: AsyncSession | None = None
) -> CalendarAccountOut | None:
    async with session_for(ctx, session) as s:
        row = (
            (
                await s.execute(
                    select(_accounts).where(
                        _accounts.c.id == account_id, _accounts.c.deleted_at.is_(None)
                    )
                )
            )
            .mappings()
            .first()
        )
    return None if row is None else _account_out(row)


async def _account_by_connection(s: AsyncSession, connection_id: UUID) -> Any:
    row = (
        (await s.execute(select(_accounts).where(_accounts.c.connection_id == connection_id)))
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound("calendar_accounts", connection_id)
    return row


def _credentials(tokens: TokenSet, previous: Mapping[str, Any] | None) -> dict[str, Any]:
    """What the connection stores: Google may leave the refresh token out of a refresh
    answer, so the one already held is kept."""
    refresh = tokens.refresh_token or (previous or {}).get("refresh_token")
    return {
        "access_token": tokens.access_token,
        "refresh_token": refresh,
        "expires_at": tokens.expires_at.isoformat(),
        "scope": tokens.scope,
    }


async def connect_account(
    ctx: WorkspaceContext,
    tokens: TokenSet,
    calendars: Sequence[CalendarInfo],
    *,
    session: AsyncSession | None = None,
) -> CalendarAccountOut:
    """A Google account as the OAuth exchange leaves it: its connection (account = the
    primary calendar's id, the address) with the tokens sealed, and its `calendar_accounts`
    row `connected`. A first connect selects the primary calendar; a reconnect keeps the
    selection (within the calendars still listed)."""
    primary = next((c for c in calendars if c.primary), calendars[0] if calendars else None)
    if primary is None:
        raise ValueError("a Google account lists at least its primary calendar")
    listed = [
        CalendarOut(id=c.id, summary=c.summary, primary=c.primary, time_zone=c.time_zone)
        for c in calendars
    ]
    async with session_for(ctx, session) as s:
        connection_id = await integrations.upsert_connection(
            ctx, kind="calendar", provider=PROVIDER, account=primary.id, status="ok", session=s
        )
        previous = await integrations.get_credentials(ctx, connection_id, session=s)
        await integrations.put_credentials(
            ctx, connection_id, _credentials(tokens, previous), session=s
        )
        insert = pg_insert(_accounts).values(
            connection_id=connection_id,
            google_email=primary.id,
            calendars=[c.model_dump(mode="json") for c in listed],
            selected_calendar_ids=[primary.id],
            status="connected",
        )
        row = (
            (
                await s.execute(
                    insert.on_conflict_do_update(
                        index_elements=[_accounts.c.workspace_id, _accounts.c.connection_id],
                        set_={
                            "google_email": insert.excluded.google_email,
                            "calendars": insert.excluded.calendars,
                            "status": "connected",
                            "deleted_at": None,
                        },
                    ).returning(*_accounts.c)
                )
            )
            .mappings()
            .one()
        )
    return _account_out(row)


async def select_calendars(
    ctx: WorkspaceContext,
    account_id: UUID,
    selected: Sequence[str],
    *,
    expected_version: int,
    session: AsyncSession | None = None,
) -> CalendarAccountOut:
    """Choose the account's calendars to sync (versioned). Events of a calendar that is no
    longer chosen are soft-deleted; the next sync reads the new choice. ValueError names a
    calendar the account does not list."""
    async with session_for(ctx, session) as s:
        current = await get_account(ctx, account_id, session=s)
        if current is None:
            raise NotFound("calendar_accounts", account_id)
        known = {c.id for c in current.calendars}
        unknown = sorted(set(selected) - known)
        if unknown:
            raise ValueError(f"not calendars of this account: {', '.join(unknown)}")
        chosen = [c.id for c in current.calendars if c.id in set(selected)]
        row = await update_versioned(
            s, _accounts, account_id, expected_version, {"selected_calendar_ids": chosen}
        )
        dropped = sorted(set(current.selected_calendar_ids) - set(chosen))
        if dropped:
            await s.execute(
                update(_events)
                .where(
                    _events.c.connection_id == current.connection_id,
                    _events.c.calendar_id.in_(dropped),
                    _events.c.deleted_at.is_(None),
                )
                .values(deleted_at=func.now())
            )
    return _account_out(row)


async def build_connector(
    ctx: WorkspaceContext,
    connection_id: UUID,
    *,
    api: GoogleCalendarPort,
    clock: Clock | None = None,
    session: AsyncSession | None = None,
) -> GoogleCalendar:
    """The connection's connector: its selected calendars, access token and status."""
    async with session_for(ctx, session) as s:
        account = await _account_by_connection(s, connection_id)
        credentials = await integrations.get_credentials(ctx, connection_id, session=s) or {}
    return GoogleCalendar(
        api=api,
        access_token=credentials.get("access_token", ""),
        calendar_ids=list(account["selected_calendar_ids"]),
        self_email=account["google_email"],
        clock=clock,
        status=account["status"],
    )


# --- Sync bookkeeping (the calendar workflows' writes) ----------------------------------------


async def oauth_client(ctx: WorkspaceContext) -> OAuthClient | None:
    """The workspace's Google OAuth client (Settings > Calendar), or None while unset."""
    found = await get_setting(ctx, OAUTH_SECTION, GoogleOAuthClient)
    if found is None or not found.value.client_id:
        return None
    return OAuthClient(
        client_id=found.value.client_id, client_secret=SecretStr(found.value.client_secret)
    )


async def account_tokens(
    ctx: WorkspaceContext, connection_id: UUID, *, session: AsyncSession | None = None
) -> dict[str, Any]:
    """The account's stored tokens (`access_token`, `refresh_token`, `expires_at`)."""
    return await integrations.get_credentials(ctx, connection_id, session=session) or {}


async def store_tokens(
    ctx: WorkspaceContext,
    connection_id: UUID,
    tokens: TokenSet,
    *,
    session: AsyncSession | None = None,
) -> None:
    async with session_for(ctx, session) as s:
        previous = await integrations.get_credentials(ctx, connection_id, session=s)
        await integrations.put_credentials(
            ctx, connection_id, _credentials(tokens, previous), session=s
        )


async def mark_needs_reauth(
    ctx: WorkspaceContext, connection_id: UUID, *, session: AsyncSession | None = None
) -> None:
    """Google refused the refresh token: the account needs a new consent (Settings shows
    Reconnect); its sync stops until then."""
    async with session_for(ctx, session) as s:
        await s.execute(
            update(_accounts)
            .where(_accounts.c.connection_id == connection_id)
            .values(status="needs_reauth")
        )
        await integrations.set_connection_status(
            ctx, connection_id, "needs_reauth", last_error="invalid_grant", session=s
        )
        await integrations.save_sync_cursor(ctx, connection_id, None, session=s)


async def complete_sync(
    ctx: WorkspaceContext,
    connection_id: UUID,
    *,
    at: datetime,
    window: tuple[datetime, datetime],
    session: AsyncSession | None = None,
) -> None:
    """A finished sync: the cursor cleared, `last_sync_at` set and one `calendar.synced`,
    in one transaction."""
    async with session_for(ctx, session) as s:
        await integrations.save_sync_cursor(ctx, connection_id, None, session=s)
        await s.execute(
            update(_accounts)
            .where(_accounts.c.connection_id == connection_id)
            .values(last_sync_at=at)
        )
        await integrations.set_connection_status(
            ctx, connection_id, "ok", last_sync_at=at, session=s
        )
        await emit(
            s,
            CalendarSyncedV1(
                connection_id=connection_id, window=SyncWindow(start=window[0], end=window[1])
            ),
            occurred_at=at,
        )


async def start_sync(
    ctx: WorkspaceContext, connection_id: UUID, *, now: datetime
) -> tuple[datetime, datetime]:
    """A new sync's window (yesterday to today + 14 days in the workspace's timezone) and
    its first cursor, stored; returns the window."""
    tz = ZoneInfo((await auth.get_workspace_settings(ctx)).timezone)
    window = sync_window(now.astimezone(tz).date(), tz)
    first = CalendarCursor(calendar_index=0, window_start=window[0], window_end=window[1])
    await integrations.save_sync_cursor(ctx, connection_id, first.model_dump(mode="json"))
    return window


async def sync_next_page(
    ctx: WorkspaceContext, connection_id: UUID, *, api: GoogleCalendarPort, clock: Clock
) -> bool:
    """Fetch the page the stored cursor points at, then store its events and the next
    cursor in one transaction (a crash resumes after the last committed page). Returns
    whether more pages follow."""
    connector = await build_connector(ctx, connection_id, api=api, clock=clock)
    cursor = await integrations.get_sync_cursor(ctx, connection_id)
    page = await connector.sync(cursor)
    following = page.next_cursor
    if not page.has_more:  # a finished cursor, so a re-run of this page fetches nothing
        done = CalendarCursor.model_validate(cursor) if cursor else connector.first_cursor()
        following = done.model_copy(
            update={"calendar_index": len(connector.calendar_ids), "page_token": None}
        ).model_dump(mode="json")
    async with tenant_session(ctx) as s:
        await ingest_events_page(ctx, connection_id, connector, page, session=s)
        await integrations.save_sync_cursor(
            ctx, connection_id, following, at=clock.now(), items=len(page.items), session=s
        )
    return page.has_more


async def account_status(
    ctx: WorkspaceContext, connection_id: UUID, *, session: AsyncSession | None = None
) -> AccountStatus:
    async with session_for(ctx, session) as s:
        status: AccountStatus = (await _account_by_connection(s, connection_id))["status"]
    return status


async def connected_accounts(
    ctx: WorkspaceContext, *, session: AsyncSession | None = None
) -> list[UUID]:
    """The connections of the workspace's `connected` accounts (the scheduled sync's)."""
    async with session_for(ctx, session) as s:
        rows = await s.execute(
            select(_accounts.c.connection_id).where(
                _accounts.c.deleted_at.is_(None), _accounts.c.status == "connected"
            )
        )
        return list(rows.scalars())


# --- OAuth over HTTP (the api half; the worker exchanges the code) -----------------------------


class OAuthNotConfigured(Exception):  # noqa: N818  # reads as the condition
    """Settings > Calendar has no Google OAuth client yet."""


async def start_connect(ctx: WorkspaceContext, *, base_url: str, now: datetime) -> str:
    """The consent URL for connecting one more Google account (a pending grant with PKCE
    is stored first). OAuthNotConfigured without a client."""
    client = await oauth_client(ctx)
    if client is None:
        raise OAuthNotConfigured
    redirect_uri = base_url.rstrip("/") + CALLBACK_PATH
    started = await integrations.begin_oauth(ctx, PROVIDER, redirect_uri=redirect_uri, now=now)
    return consent_url(
        client_id=client.client_id,
        redirect_uri=redirect_uri,
        state=started.state,
        code_challenge=started.code_challenge,
    )


async def accept_callback(
    ctx: WorkspaceContext, *, state: str, code: str, now: datetime
) -> UUID | None:
    """Store the code of the consent `state` names (sealed, once, within ten minutes) and
    enqueue the worker's exchange; None for an unknown, used or expired state. Makes no
    outbound call (architecture principle 3)."""
    async with tenant_session(ctx) as s:
        pending_id = await integrations.accept_oauth_code(
            ctx, PROVIDER, state=state, code=code, now=now, session=s
        )
    if pending_id is None:
        return None
    await _enqueue(EXCHANGE_WORKFLOW, f"calendar-oauth:{pending_id}", ctx, pending_id)
    return pending_id


async def request_sync(
    ctx: WorkspaceContext, account_id: UUID, *, now: datetime
) -> CalendarAccountOut:
    """Sync now: enqueue the account's sync (one per account and second)."""
    account = await get_account(ctx, account_id)
    if account is None:
        raise NotFound("calendar_accounts", account_id)
    workflow_id = f"calendar-sync:{account.connection_id}:{now.isoformat(timespec='seconds')}"
    await _enqueue(SYNC_WORKFLOW, workflow_id, ctx, account.connection_id)
    return account


async def _enqueue(workflow: str, workflow_id: str, ctx: WorkspaceContext, arg: UUID) -> None:
    await deadletter.dbos_client().enqueue_async(
        {"queue_name": SYNC_QUEUE, "workflow_name": workflow, "workflow_id": workflow_id},
        str(ctx.workspace_id),
        str(arg),
    )


def _real_connector(**deps: Any) -> GoogleCalendar:
    return GoogleCalendar(**deps)


# The fake connector replays account a of the recordings over the recorded window.
_FAKE_ACCOUNT = "avery@example.com"
_FAKE_WINDOW = (datetime(2026, 3, 8, 5, 0, tzinfo=UTC), datetime(2026, 3, 24, 4, 0, tzinfo=UTC))


def _fake_connector(**deps: Any) -> GoogleCalendar:
    deps.setdefault("api", FakeGoogleCalendar())
    deps.setdefault("access_token", "fake-access-a")
    deps.setdefault("calendar_ids", [_FAKE_ACCOUNT])
    deps.setdefault("self_email", _FAKE_ACCOUNT)
    deps.setdefault("window", _FAKE_WINDOW)
    return GoogleCalendar(**deps)


register_connector(PROVIDER, "calendar", real=_real_connector, fake=_fake_connector)
