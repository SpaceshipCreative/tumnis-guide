"""integrations public functions and DTOs; the only file other modules may import.

The canonical integration model (P0-12): a connector is a small `Connector` protocol plus
a `register_connector` entry; `ingest_page` stores a page's raw payloads, maps them to
canonical records and upserts the records integrations owns (people, threads, messages,
notes, artifacts). Events belong to calendar and documents to knowledge: their modules
upsert them with the same helper (`tumnis.core.canonical.upsert_records`) and call
`store_raw_payloads` here for the payloads. `link_context` ties a task, project or
proposal to a record, inheriting its taint.

Every function takes the workspace context and runs in its own transaction, or in the
caller's `session` (already in that workspace) when one is passed, so a sync step can
write records, cursor and events in one transaction (P3-02).
"""

import base64
import hashlib
import json
import secrets
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Final, Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import ColumnElement, Table, and_, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.adapters.registry import Health, current_mode, register_adapter, resolve
from tumnis.core.canonical import (
    CANONICAL_KEY,
    CanonicalRecord,
    UpsertStats,
    soft_delete_records,
    upsert_records,
)
from tumnis.core.schemas import versioned
from tumnis.core.settings_store import open_for_workspace, seal_for_workspace
from tumnis.core.tenancy import WorkspaceContext, session_for
from tumnis.core.versioning import NotFound
from tumnis.modules.integrations import rules
from tumnis.modules.integrations.models import (
    Artifact,
    Connection,
    ContextItem,
    Message,
    Note,
    OAuthPending,
    Person,
    RawPayload,
    SyncState,
    Thread,
)

ConnectorKind = Literal["email", "notes", "chat", "calendar", "code", "deploy", "knowledge"]
Capability = Literal["poll", "webhook", "read", "write"]
OwnerType = Literal["task", "project", "proposal"]
TargetType = Literal["message", "thread", "note", "person", "artifact", "event", "document", "url"]


# --- Raw items and pages -------------------------------------------------------------------


class RawItem(BaseModel):
    """One provider object as fetched: kept in `raw_payloads` for re-normalizing."""

    external_id: str
    record_type: str
    payload: dict[str, Any]
    fetched_at: AwareDatetime


class SyncPage(BaseModel):
    items: list[RawItem]
    deleted: list[str] = Field(default_factory=list)  # external ids the provider reports as gone
    next_cursor: dict[str, Any] | None
    has_more: bool


# --- Canonical records owned by integrations ------------------------------------------------


@versioned("entities", "person", 1)
class PersonRecord(CanonicalRecord):
    """A person, upserted on workspace + primary email (lower-cased by the connector)."""

    schema_version: Literal[1] = 1
    record_type: Literal["person"] = "person"
    display_name: str | None = None
    primary_email: str
    emails: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)


@versioned("entities", "thread", 1)
class ThreadRecord(CanonicalRecord):
    schema_version: Literal[1] = 1
    record_type: Literal["thread"] = "thread"
    subject: str | None = None
    participants: list[str] = Field(default_factory=list)
    last_message_at: AwareDatetime | None = None


@versioned("entities", "message", 1)
class MessageRecord(CanonicalRecord):
    """An email or chat message. `body_html_sanitized` is filled by the connector's `map`
    through `tumnis.core.sanitize.sanitize_html` (pure)."""

    schema_version: Literal[1] = 1
    record_type: Literal["message"] = "message"
    thread_external_id: str | None = None
    sent_at: AwareDatetime | None = None
    from_addr: str | None = None
    to_addrs: list[str] = Field(default_factory=list)
    subject: str | None = None
    body_text: str | None = None
    body_html_sanitized: str | None = None
    labels: list[str] = Field(default_factory=list)


@versioned("entities", "note", 1)
class NoteRecord(CanonicalRecord):
    """Meeting notes (Granola) with their action items."""

    schema_version: Literal[1] = 1
    record_type: Literal["note"] = "note"
    title: str | None = None
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    attendees: list[str] = Field(default_factory=list)
    body_text: str | None = None
    action_items: list[str] = Field(default_factory=list)
    event_external_id: str | None = None


@versioned("entities", "artifact", 1)
class ArtifactRecord(CanonicalRecord):
    """A pull request, deployment or similar object with a state (GitHub, Coolify)."""

    schema_version: Literal[1] = 1
    record_type: Literal["artifact"] = "artifact"
    kind: str
    url: str | None = None
    state: str | None = None
    checks: dict[str, Any] = Field(default_factory=dict)


# --- Connectors -------------------------------------------------------------------------------


class Connector(Protocol):
    kind: ConnectorKind
    provider: str
    capabilities: frozenset[Capability]

    async def sync(self, cursor: dict[str, Any] | None) -> SyncPage: ...

    def map(self, raw: RawItem) -> list[CanonicalRecord]: ...  # pure

    async def health(self) -> Health: ...


ConnectorFactory = Callable[..., Connector]


@dataclass(frozen=True)
class ConnectorSpec:
    provider: str
    kind: ConnectorKind
    adapter_name: str


_CONNECTORS: dict[str, ConnectorSpec] = {}


class ConnectorWithoutFake(ValueError):  # noqa: N818  # reads as the condition it reports
    """A connector registered without a fake: fake mode (REL-7) would have nothing to run."""


def connector_adapter_name(provider: str) -> str:
    return f"integrations.connector.{provider}"


def register_connector(
    provider: str,
    kind: ConnectorKind,
    *,
    real: ConnectorFactory,
    fake: ConnectorFactory | None = None,
) -> None:
    """Registers in the adapter registry as 'integrations.connector.<provider>' (so fake
    mode and the fake/contract meta-tests apply) and in the connector index."""
    if fake is None:
        raise ConnectorWithoutFake(
            f"connector {provider!r} needs a fake (TUMNIS_ADAPTERS=fake runs it in previews)"
        )
    name = connector_adapter_name(provider)
    register_adapter(name, port=Connector, real=real, fake=fake)
    _CONNECTORS[provider] = ConnectorSpec(provider=provider, kind=kind, adapter_name=name)


def connectors() -> Mapping[str, ConnectorSpec]:
    """The connector index: provider -> spec."""
    return dict(_CONNECTORS)


def connector_for(provider: str, **deps: Any) -> Connector:
    """The registered connector for `provider` in the mode `TUMNIS_ADAPTERS` selects."""
    connector: Connector = resolve(_CONNECTORS[provider].adapter_name, current_mode(), **deps)
    return connector


# --- Ingest -----------------------------------------------------------------------------------


class RecordTypeNotOwned(Exception):  # noqa: N818  # the plan's name
    """A record of a type another module owns reached integrations' ingest."""

    def __init__(self, record_type: str, owner: str) -> None:
        super().__init__(
            f"{record_type!r} records belong to the {owner} module; upsert them through {owner}.api"
        )
        self.record_type = record_type
        self.owner = owner


class IngestResult(BaseModel):
    raw_stored: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    changed_ids: list[UUID] = Field(default_factory=list)


RawKey = tuple[str, str]  # (record_type, external_id)

_connections: Table = Connection.__table__  # type: ignore[assignment]
_raw: Table = RawPayload.__table__  # type: ignore[assignment]
_context: Table = ContextItem.__table__  # type: ignore[assignment]
_RAW_KEY = (_raw.c.connection_id, _raw.c.record_type, _raw.c.external_id)
_TABLES: dict[str, Table] = {
    "person": Person.__table__,  # type: ignore[dict-item]
    "thread": Thread.__table__,  # type: ignore[dict-item]
    "message": Message.__table__,  # type: ignore[dict-item]
    "note": Note.__table__,  # type: ignore[dict-item]
    "artifact": Artifact.__table__,  # type: ignore[dict-item]
}


async def connection_source(session: AsyncSession, connection_id: UUID) -> str:
    """The `source` value records from this connection carry ("email:inbox_zero");
    NotFound when the connection is not in the workspace."""
    row = (
        await session.execute(
            select(_connections.c.kind, _connections.c.provider).where(
                _connections.c.id == connection_id, _connections.c.deleted_at.is_(None)
            )
        )
    ).one_or_none()
    if row is None:
        raise NotFound("connections", connection_id)
    return f"{row.kind}:{row.provider}"


async def connection_accounts(
    session: AsyncSession, connection_ids: Collection[UUID]
) -> dict[UUID, str]:
    """The account (an address, "seed" for seed data) of each connection, in one query
    whatever the number of ids (P1-10: the day calendar names each event's account)."""
    rows = await session.execute(
        select(_connections.c.id, _connections.c.account).where(
            _connections.c.id.in_(list(connection_ids))
        )
    )
    return {row.id: row.account for row in rows}


async def seed_connection(
    session: AsyncSession, kind: ConnectorKind, provider: str, account: str
) -> UUID:
    """The connection seed records hang off (P0-02's seed sets load events through it):
    one per (workspace, provider, account), created on first use with status `ok`. A dev
    and test tool, not a connector; it holds no credentials."""
    await session.execute(
        pg_insert(_connections)
        .values(kind=kind, provider=provider, account=account, status="ok")
        .on_conflict_do_nothing(index_elements=["workspace_id", "provider", "account"])
    )
    connection_id: UUID = (
        await session.execute(
            select(_connections.c.id).where(
                _connections.c.provider == provider, _connections.c.account == account
            )
        )
    ).scalar_one()
    return connection_id


async def store_raw_payloads(
    ctx: WorkspaceContext,
    connection_id: UUID,
    items: Sequence[RawItem],
    *,
    session: AsyncSession | None = None,
) -> dict[RawKey, UUID]:
    """Keeps each item's provider JSON in `raw_payloads` (LZ4-compressed), one row per
    (connection, record type, external id) holding the latest fetch. Returns the row ids.
    A payload that did not change is not rewritten."""
    latest = {(item.record_type, item.external_id): item for item in items}  # last one wins
    if not latest:
        return {}
    async with session_for(ctx, session) as s:
        await connection_source(s, connection_id)
        insert = pg_insert(_raw).values(
            [
                {
                    "connection_id": connection_id,
                    "record_type": item.record_type,
                    "external_id": item.external_id,
                    "payload": item.payload,
                    "fetched_at": item.fetched_at,
                }
                for item in latest.values()
            ]
        )
        upsert = insert.on_conflict_do_update(
            index_elements=[_raw.c.workspace_id, *_RAW_KEY],
            set_={
                "payload": insert.excluded.payload,
                "fetched_at": insert.excluded.fetched_at,
                "deleted_at": None,
            },
            where=or_(
                _raw.c.payload.is_distinct_from(insert.excluded.payload),
                _raw.c.deleted_at.is_not(None),
            ),
        ).returning(_raw.c.record_type, _raw.c.external_id, _raw.c.id)
        ids = {(t, e): row_id for t, e, row_id in (await s.execute(upsert)).all()}
        unchanged = [key for key in latest if key not in ids]
        if unchanged:
            existing = select(_raw.c.record_type, _raw.c.external_id, _raw.c.id).where(
                _raw.c.connection_id == connection_id,
                tuple_(_raw.c.record_type, _raw.c.external_id).in_(unchanged),
            )
            ids |= {(t, e): row_id for t, e, row_id in (await s.execute(existing)).all()}
    return ids


# Upserted in this order, so a message can point at a thread from the same page.
_OWN_ORDER: tuple[str, ...] = ("person", "thread", "message", "note", "artifact")
_PEOPLE_KEY: tuple[str, ...] = ("workspace_id", "primary_email")


def _columns(rec: CanonicalRecord, thread_ids: Mapping[str, UUID]) -> dict[str, Any]:
    """The table-specific columns of an integrations-owned record."""
    match rec:
        case PersonRecord():
            return {
                "display_name": rec.display_name,
                "primary_email": rec.primary_email,
                "emails": rec.emails,
                "domains": rec.domains,
            }
        case ThreadRecord():
            return {
                "subject": rec.subject,
                "participants": rec.participants,
                "last_message_at": rec.last_message_at,
            }
        case MessageRecord():
            return {
                "thread_id": thread_ids.get(rec.thread_external_id or ""),
                "sent_at": rec.sent_at,
                "from_addr": rec.from_addr,
                "to_addrs": rec.to_addrs,
                "subject": rec.subject,
                "body_text": rec.body_text,
                "body_html_sanitized": rec.body_html_sanitized,
                "labels": rec.labels,
            }
        case NoteRecord():
            return {
                "title": rec.title,
                "start_at": rec.start_at,
                "end_at": rec.end_at,
                "attendees": rec.attendees,
                "body_text": rec.body_text,
                "action_items": rec.action_items,
                "event_external_id": rec.event_external_id,
            }
        case ArtifactRecord():
            return {"kind": rec.kind, "url": rec.url, "state": rec.state, "checks": rec.checks}
    raise TypeError(f"{type(rec).__qualname__} is not the model for {rec.record_type!r} records")


async def _thread_ids(
    s: AsyncSession, connection_id: UUID, records: Sequence[CanonicalRecord]
) -> dict[str, UUID]:
    wanted = {
        rec.thread_external_id
        for rec in records
        if isinstance(rec, MessageRecord) and rec.thread_external_id
    }
    if not wanted:
        return {}
    threads = _TABLES["thread"]
    rows = await s.execute(
        select(threads.c.external_id, threads.c.id).where(
            threads.c.connection_id == connection_id, threads.c.external_id.in_(sorted(wanted))
        )
    )
    return dict(rows.all())


async def ingest_page(
    ctx: WorkspaceContext,
    connection_id: UUID,
    connector: Connector,
    page: SyncPage,
    *,
    session: AsyncSession | None = None,
) -> IngestResult:
    """Stores raw payloads, maps, upserts integrations-owned records, soft-deletes
    page.deleted. Records owned by another module (event, document) raise
    RecordTypeNotOwned before anything is written: calendar and knowledge connectors call
    their own module's upsert with the same helper."""
    by_type: dict[str, list[tuple[CanonicalRecord, RawKey]]] = {}
    for item in page.items:
        for rec in connector.map(item):
            owner = rules.record_owner(rec.record_type)
            if owner != "integrations":
                raise RecordTypeNotOwned(rec.record_type, owner)
            by_type.setdefault(rec.record_type, []).append(
                (rec, (item.record_type, item.external_id))
            )

    async with session_for(ctx, session) as s:
        source = await connection_source(s, connection_id)
        raw_ids = await store_raw_payloads(ctx, connection_id, page.items, session=s)
        stats = UpsertStats()
        for record_type in _OWN_ORDER:
            pairs = by_type.get(record_type, [])
            if not pairs:
                continue
            records = [rec for rec, _ in pairs]
            thread_ids = await _thread_ids(s, connection_id, records)
            stats += await upsert_records(
                s,
                _TABLES[record_type],
                connection_id,
                records,
                {rec.external_id: raw_ids[key] for rec, key in pairs},
                partial(_columns, thread_ids=thread_ids),
                source=source,
                conflict=_PEOPLE_KEY if record_type == "person" else CANONICAL_KEY,
            )
        deleted = 0
        for record_type in _OWN_ORDER:
            deleted += await soft_delete_records(
                s, _TABLES[record_type], connection_id, page.deleted
            )
    return IngestResult(
        raw_stored=len(raw_ids),
        inserted=stats.inserted,
        updated=stats.updated,
        unchanged=stats.unchanged,
        deleted=deleted,
        changed_ids=list(stats.changed_ids),
    )


# --- Context items ----------------------------------------------------------------------------


class ContextItemOut(BaseModel):
    id: UUID
    owner_type: OwnerType
    owner_id: UUID
    target_type: TargetType
    target_id: UUID | None
    target_url: str | None
    tainted: bool
    added_by: str
    created_at: datetime
    version: int


TaintLookup = Callable[[AsyncSession, UUID], Awaitable[bool | None]]
_TAINT_LOOKUPS: dict[str, TaintLookup] = {}


def register_target_taint(target_type: TargetType, lookup: TaintLookup) -> None:
    """A module that owns a target type (calendar: event, knowledge: document) tells
    `link_context` how to read a target's taint; None means no such row."""
    _TAINT_LOOKUPS[target_type] = lookup


async def link_context(
    ctx: WorkspaceContext,
    *,
    owner_type: OwnerType,
    owner_id: UUID,
    target_type: TargetType,
    target_id: UUID | None = None,
    target_url: str | None = None,
    added_by: str,
    session: AsyncSession | None = None,
) -> ContextItemOut:
    """Idempotent on (owner, target). tainted = target.tainted (rules.propagate_taint).
    A record target needs `target_id` (NotFound when no such row is visible); a bare
    `url` target needs `target_url` and is tainted (outside content). Linking again
    returns the existing item (restoring it if it was deleted)."""
    if (target_type == "url") != (target_id is None) or (target_type == "url") != (
        target_url is not None
    ):
        raise ValueError("a url target takes target_url only; a record target takes target_id")
    async with session_for(ctx, session) as s:
        if target_id is None:
            tainted = rules.propagate_taint(True)
            key = _context.c.target_url
            where = _context.c.target_id.is_(None)
        else:
            tainted = rules.propagate_taint(await _target_taint(s, target_type, target_id))
            key = _context.c.target_id
            where = _context.c.target_id.is_not(None)
        insert = pg_insert(_context).values(
            owner_type=owner_type,
            owner_id=owner_id,
            target_type=target_type,
            target_id=target_id,
            target_url=target_url,
            tainted=tainted,
            added_by=added_by,
        )
        upsert = insert.on_conflict_do_update(
            index_elements=[
                _context.c.workspace_id,
                _context.c.owner_type,
                _context.c.owner_id,
                _context.c.target_type,
                key,
            ],
            index_where=where,
            set_={"deleted_at": None},
            where=_context.c.deleted_at.is_not(None),
        )
        await s.execute(upsert)
        row = (
            await s.execute(
                select(_context).where(
                    _context.c.owner_type == owner_type,
                    _context.c.owner_id == owner_id,
                    _context.c.target_type == target_type,
                    key == (target_url if target_id is None else target_id),
                    where,
                )
            )
        ).one()
    return ContextItemOut.model_validate(row._mapping)


async def get_context_item_ref(
    ctx: WorkspaceContext, context_item_id: UUID, *, session: AsyncSession | None = None
) -> ContextItemOut | None:
    """A live context item of the caller's workspace, or None: how another module checks
    an id it is asked to link (tasks, P0-18, FR-14.2)."""
    async with session_for(ctx, session) as s:
        row = (
            await s.execute(
                select(_context).where(
                    _context.c.id == context_item_id, _context.c.deleted_at.is_(None)
                )
            )
        ).first()
    return None if row is None else ContextItemOut.model_validate(row._mapping)


async def _target_taint(s: AsyncSession, target_type: str, target_id: UUID) -> bool:
    if target_type in _TABLES:
        table = _TABLES[target_type]
        found: bool | None = await s.scalar(select(table.c.tainted).where(table.c.id == target_id))
    else:
        lookup = _TAINT_LOOKUPS.get(target_type)
        if lookup is None:
            raise LookupError(f"no module registered the taint of {target_type!r} targets")
        found = await lookup(s, target_id)
    if found is None:
        raise NotFound(target_type, target_id)
    return found


# --- Connections, credentials and sync cursors (P1-09; P3-02 reuses them) --------------------

_sync_state: Table = SyncState.__table__  # type: ignore[assignment]
ConnectionStatus = Literal["pending_auth", "ok", "needs_reauth", "error"]


async def upsert_connection(
    ctx: WorkspaceContext,
    *,
    kind: ConnectorKind,
    provider: str,
    account: str,
    status: ConnectionStatus = "ok",
    session: AsyncSession | None = None,
) -> UUID:
    """The connection for (provider, account), created or brought back (a reconnected
    account keeps its id, so its records stay attached); returns its id."""
    async with session_for(ctx, session) as s:
        insert = pg_insert(_connections).values(
            kind=kind, provider=provider, account=account, status=status
        )
        upsert = insert.on_conflict_do_update(
            index_elements=["workspace_id", "provider", "account"],
            set_={"kind": kind, "status": status, "deleted_at": None, "last_error": None},
        ).returning(_connections.c.id)
        connection_id: UUID = (await s.execute(upsert)).scalar_one()
    return connection_id


def _live_connection(connection_id: UUID) -> ColumnElement[bool]:
    """The connection, unless soft-deleted: its tokens are neither opened nor rewritten
    (a reconnect brings the row back through `upsert_connection` first)."""
    return and_(_connections.c.id == connection_id, _connections.c.deleted_at.is_(None))


def _credentials_aad(connection_id: UUID) -> bytes:
    return b"connections:" + str(connection_id).encode()


async def put_credentials(
    ctx: WorkspaceContext,
    connection_id: UUID,
    credentials: Mapping[str, Any],
    *,
    session: AsyncSession | None = None,
) -> None:
    """Seal the connection's credentials (tokens) with the workspace data key into
    `credentials_enc` (Data flow rule 5): no column holds them in plaintext."""
    async with session_for(ctx, session) as s:
        key_version, sealed = await seal_for_workspace(
            s,
            ctx.workspace_id,
            json.dumps(dict(credentials)).encode(),
            aad=_credentials_aad(connection_id),
        )
        written = await s.execute(
            update(_connections)
            .where(_live_connection(connection_id))
            .values(credentials_enc=sealed, key_version=key_version)
            .returning(_connections.c.id)
        )
        if written.one_or_none() is None:
            raise NotFound("connections", connection_id)


async def get_credentials(
    ctx: WorkspaceContext, connection_id: UUID, *, session: AsyncSession | None = None
) -> dict[str, Any] | None:
    """The connection's credentials, opened with the workspace key; None when unset."""
    async with session_for(ctx, session) as s:
        sealed = await s.scalar(
            select(_connections.c.credentials_enc).where(_live_connection(connection_id))
        )
        if sealed is None:
            return None
        plaintext = await open_for_workspace(
            s, ctx.workspace_id, bytes(sealed), aad=_credentials_aad(connection_id)
        )
    credentials: dict[str, Any] = json.loads(plaintext)
    return credentials


async def set_connection_status(
    ctx: WorkspaceContext,
    connection_id: UUID,
    status: ConnectionStatus,
    *,
    last_error: str | None = None,
    last_sync_at: datetime | None = None,
    session: AsyncSession | None = None,
) -> None:
    values: dict[str, Any] = {"status": status, "last_error": last_error}
    if last_sync_at is not None:
        values["last_sync_at"] = last_sync_at
    async with session_for(ctx, session) as s:
        await s.execute(
            update(_connections).where(_live_connection(connection_id)).values(**values)
        )


async def get_sync_cursor(
    ctx: WorkspaceContext, connection_id: UUID, *, session: AsyncSession | None = None
) -> dict[str, Any] | None:
    """Where the connection's running sync is (None: no sync in progress)."""
    async with session_for(ctx, session) as s:
        cursor: dict[str, Any] | None = await s.scalar(
            select(_sync_state.c.cursor).where(_sync_state.c.connection_id == connection_id)
        )
    return cursor


async def save_sync_cursor(
    ctx: WorkspaceContext,
    connection_id: UUID,
    cursor: Mapping[str, Any] | None,
    *,
    at: datetime | None = None,
    items: int = 0,
    session: AsyncSession | None = None,
) -> None:
    """Store the cursor (one `sync_state` row per connection) in the caller's transaction,
    with the page's records, so a crash resumes after the last committed page."""
    async with session_for(ctx, session) as s:
        insert = pg_insert(_sync_state).values(
            connection_id=connection_id,
            cursor=None if cursor is None else dict(cursor),
            last_page_at=at,
            items_seen=items,
        )
        await s.execute(
            insert.on_conflict_do_update(
                index_elements=[_sync_state.c.workspace_id, _sync_state.c.connection_id],
                set_={
                    "cursor": insert.excluded.cursor,
                    "last_page_at": func.coalesce(
                        insert.excluded.last_page_at, _sync_state.c.last_page_at
                    ),
                    "items_seen": _sync_state.c.items_seen + insert.excluded.items_seen,
                },
            )
        )


# --- OAuth grants in flight (P1-09; Google Docs reuses them in P3-02) ---------------------------

OAUTH_PENDING_TTL: Final = timedelta(minutes=10)  # plan default
_pending: Table = OAuthPending.__table__  # type: ignore[assignment]


class OAuthStart(BaseModel):
    pending_id: UUID
    state: str
    code_challenge: str  # S256 of the verifier, base64url without padding


class OAuthGrant(BaseModel):
    """What the worker exchanges: the code, the PKCE verifier and the redirect URI."""

    provider: str
    code: str
    code_verifier: str
    redirect_uri: str


def _state_hash(state: str) -> bytes:
    return hashlib.sha256(state.encode()).digest()


def _pending_aad(pending_id: UUID, field: str) -> bytes:
    return f"oauth_pending:{pending_id}:{field}".encode()


async def begin_oauth(
    ctx: WorkspaceContext,
    provider: str,
    *,
    redirect_uri: str,
    now: datetime,
    session: AsyncSession | None = None,
) -> OAuthStart:
    """A consent in flight: a random `state` (only its hash is kept) and a PKCE verifier
    (sealed); valid for OAUTH_PENDING_TTL."""
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    async with session_for(ctx, session) as s:
        pending_id: UUID = (
            await s.execute(
                pg_insert(_pending)
                .values(
                    provider=provider,
                    state_hash=_state_hash(state),
                    key_version=0,
                    redirect_uri=redirect_uri,
                    expires_at=now + OAUTH_PENDING_TTL,
                )
                .returning(_pending.c.id)
            )
        ).scalar_one()
        key_version, sealed = await seal_for_workspace(
            s, ctx.workspace_id, verifier.encode(), aad=_pending_aad(pending_id, "verifier")
        )
        await s.execute(
            update(_pending)
            .where(_pending.c.id == pending_id)
            .values(verifier_enc=sealed, key_version=key_version)
        )
    return OAuthStart(
        pending_id=pending_id, state=state, code_challenge=challenge.decode().rstrip("=")
    )


class OAuthAccepted(BaseModel):
    """What the callback found: `accepted` (code stored), `declined` (the user or Google
    sent no code; the state is used up), `invalid` (used or expired) or `unknown` (no such
    consent in this workspace)."""

    outcome: Literal["accepted", "declined", "invalid", "unknown"]
    pending_id: UUID | None = None


async def accept_oauth_code(
    ctx: WorkspaceContext,
    provider: str,
    *,
    state: str,
    code: str | None,
    now: datetime,
    session: AsyncSession | None = None,
) -> OAuthAccepted:
    """The callback's half: the pending consent `state` names, if it is this provider's,
    unexpired and unused, is used up and its code stored sealed."""
    async with session_for(ctx, session) as s:
        row = (
            (
                await s.execute(
                    select(
                        _pending.c.id,
                        _pending.c.used_at,
                        _pending.c.expires_at,
                        _pending.c.deleted_at,
                    )
                    .where(
                        _pending.c.state_hash == _state_hash(state),
                        _pending.c.provider == provider,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return OAuthAccepted(outcome="unknown")
        pending_id: UUID = row["id"]
        if row["used_at"] is not None or row["deleted_at"] is not None or row["expires_at"] <= now:
            return OAuthAccepted(outcome="invalid", pending_id=pending_id)
        values: dict[str, Any] = {"used_at": now}
        if code is not None:
            key_version, sealed = await seal_for_workspace(
                s, ctx.workspace_id, code.encode(), aad=_pending_aad(pending_id, "code")
            )
            values |= {"code_enc": sealed, "key_version": key_version}
        await s.execute(update(_pending).where(_pending.c.id == pending_id).values(**values))
    return OAuthAccepted(outcome="declined" if code is None else "accepted", pending_id=pending_id)


async def read_oauth_grant(
    ctx: WorkspaceContext, pending_id: UUID, *, session: AsyncSession | None = None
) -> OAuthGrant | None:
    """The accepted grant, opened; None once consumed (or before a code arrived)."""
    async with session_for(ctx, session) as s:
        row = (
            (
                await s.execute(
                    select(_pending).where(
                        _pending.c.id == pending_id, _pending.c.deleted_at.is_(None)
                    )
                )
            )
            .mappings()
            .first()
        )
        if row is None or row["code_enc"] is None or row["verifier_enc"] is None:
            return None
        code = await open_for_workspace(
            s, ctx.workspace_id, bytes(row["code_enc"]), aad=_pending_aad(pending_id, "code")
        )
        verifier = await open_for_workspace(
            s,
            ctx.workspace_id,
            bytes(row["verifier_enc"]),
            aad=_pending_aad(pending_id, "verifier"),
        )
    return OAuthGrant(
        provider=row["provider"],
        code=code.decode(),
        code_verifier=verifier.decode(),
        redirect_uri=row["redirect_uri"],
    )


async def consume_oauth_grant(
    ctx: WorkspaceContext, pending_id: UUID, *, session: AsyncSession | None = None
) -> None:
    """The grant was exchanged: code and verifier dropped, the row soft-deleted."""
    async with session_for(ctx, session) as s:
        await s.execute(
            update(_pending)
            .where(_pending.c.id == pending_id)
            .values(code_enc=None, verifier_enc=None, deleted_at=func.now())
        )
