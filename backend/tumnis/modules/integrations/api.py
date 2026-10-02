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

import asyncio
import base64
import contextlib
import hashlib
import json
import secrets
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import Annotated, Any, Final, Literal, Protocol
from urllib.parse import urlencode
from uuid import UUID

from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import AnyUrl, AwareDatetime, BaseModel, Field, StringConstraints
from sqlalchemy import ColumnElement, Table, and_, func, or_, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import ScalarResult
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from tumnis.core import audit, deadletter
from tumnis.core.adapters.registry import Health, current_mode, register_adapter, resolve
from tumnis.core.canonical import (
    CANONICAL_KEY,
    CanonicalRecord,
    UpsertStats,
    content_hash,
    soft_delete_records,
    upsert_records,
)
from tumnis.core.clock import Clock
from tumnis.core.ids import uuid7
from tumnis.core.metrics import CONNECTOR_ITEMS, CONNECTOR_SYNC_AGE
from tumnis.core.outbox import emit
from tumnis.core.ratelimit import SlidingWindows
from tumnis.core.schemas import versioned
from tumnis.core.settings_store import open_for_workspace, seal_for_workspace
from tumnis.core.tenancy import WorkspaceContext, session_for, tenant_session
from tumnis.core.types import ActorRef
from tumnis.core.versioning import NotFound, StaleVersion, Version, update_versioned
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
from tumnis.modules.integrations.oauth_port import OAuthPort, OAuthRefused, OAuthServer
from tumnis.modules.integrations.payloads import (
    ArtifactUpdatedV1,
    ConnectionAuthRequiredV1,
    ItemsIngestedV1,
)
from tumnis.modules.projects import api as projects
from tumnis.seed import LinkSeed, register_seed_writer

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


OwnerTaint = Callable[[AsyncSession, UUID], Awaitable[None]]
_OWNER_TAINT: dict[str, OwnerTaint] = {}


def register_owner_taint(owner_type: OwnerType, raise_taint: OwnerTaint) -> None:
    """A module that owns an owner type (tasks: task) tells `link_context` how to raise an
    owner's taint when a tainted item is attached to it (P2-08, SAF-1: linking only adds
    taint, never clears it). It runs in `link_context`'s transaction."""
    _OWNER_TAINT[owner_type] = raise_taint


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
    """Idempotent on (owner, target). tainted = target.tainted (rules.propagate_taint); a
    tainted item raises its owner's taint through the owner module's
    `register_owner_taint` hook (P2-08).
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
        raise_taint = _OWNER_TAINT.get(owner_type)
        if row.tainted and raise_taint is not None:
            await raise_taint(s, owner_id)  # P2-08: the owner is now made from it too
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


async def proposal_taint(s: AsyncSession, context_item_ids: Collection[UUID]) -> bool:
    """The taint a proposal made from these context items carries (P2-08, SAF-1): the OR
    of the items' stored taint (`rules.propagate_taint`). P3-07's `create_proposal` writes
    it on the create path. NotFound for an id that is not a live item of the workspace."""
    wanted = set(context_item_ids)
    rows = (
        await s.execute(
            select(_context.c.id, _context.c.tainted).where(
                _context.c.id.in_(sorted(wanted, key=str)), _context.c.deleted_at.is_(None)
            )
        )
    ).all()
    missing = wanted - {row.id for row in rows}
    if missing:
        raise NotFound("context_items", min(missing, key=str))
    return rules.propagate_taint(*(row.tainted for row in rows))


class ContextItemText(BaseModel):
    """A context item as a task packet shows it (P2-02): what it is, where it came from,
    its text and the attributes its block names (for example `from`). The text is outside
    content: the packet builder renders it in an untrusted block."""

    id: UUID
    target_type: TargetType
    source: str  # the canonical record's source kind ("email", "chat", "note", ...)
    text: str
    tainted: bool
    provider_url: str | None
    attrs: dict[str, str]


def _kind_of(source: str | None, default: str) -> str:
    """ "email:inbox_zero" -> "email"; the target's own kind when the record names none."""
    return (source or "").split(":", 1)[0] or default


def _lines(*parts: str | None) -> str:
    return "\n".join(p for p in parts if p)


def _record_text(target_type: str, row: Any) -> tuple[str, dict[str, str], str]:
    """(text, attrs, default source) of one canonical record."""
    if target_type == "message":
        attrs = {"from": row.from_addr} if row.from_addr else {}
        return _lines(row.subject, row.body_text), attrs, "email"
    if target_type == "thread":
        return _lines(row.subject), {}, "email"
    if target_type == "note":
        return _lines(row.title, row.body_text), {}, "note"
    if target_type == "person":
        return _lines(row.display_name, row.primary_email), {}, "email"
    return _lines(row.kind, row.url, row.state), {}, "artifact"  # artifact


async def owned_context_item_ids(
    s: AsyncSession, owner_type: OwnerType, owner_id: UUID
) -> list[UUID]:
    """The live context items an owner (a task, a project, a proposal) holds, oldest first."""
    rows: ScalarResult[UUID] = await s.scalars(
        select(_context.c.id)
        .where(
            _context.c.owner_type == owner_type,
            _context.c.owner_id == owner_id,
            _context.c.deleted_at.is_(None),
        )
        .order_by(_context.c.created_at, _context.c.id)
    )
    return list(rows)


async def context_item_texts(s: AsyncSession, ids: Sequence[UUID]) -> list[ContextItemText]:
    """The live context items among `ids`, in that order, each with the text of what it
    points at: a message's subject and body, a note's title and body, a person, an
    artifact, a thread's subject; a URL's address. An event or a document (owned by
    calendar and knowledge) is named by its type and id here: its content reaches the
    packet through its own module (passages, P1-17)."""
    wanted = list(dict.fromkeys(ids))
    if not wanted:
        return []
    items = {
        row.id: row
        for row in await s.execute(
            select(_context).where(_context.c.id.in_(wanted), _context.c.deleted_at.is_(None))
        )
    }
    out: list[ContextItemText] = []
    for item_id in wanted:
        item = items.get(item_id)
        if item is None:
            continue
        attrs: dict[str, str] = {}
        text, source = item.target_url or "", str(item.target_type)
        table = _TABLES.get(item.target_type)
        if table is not None and item.target_id is not None:
            row = (
                await s.execute(
                    select(table).where(table.c.id == item.target_id, table.c.deleted_at.is_(None))
                )
            ).first()
            if row is not None:
                text, attrs, default = _record_text(item.target_type, row)
                source = _kind_of(row.source, default)
        elif item.target_id is not None:
            text = f"{item.target_type} {item.target_id}"
        out.append(
            ContextItemText(
                id=item.id,
                target_type=item.target_type,
                source=source,
                text=text,
                tainted=item.tainted,
                provider_url=item.target_url,
                attrs=attrs,
            )
        )
    return out


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

DEFAULT_SCOPE: Final = "default"

_sync_state: Table = SyncState.__table__  # type: ignore[assignment]
ConnectionStatus = rules.ConnectionStatus
SyncOutcome = rules.SyncOutcome
ConnectionSettings = rules.ConnectionSettings
ProviderLimit = rules.ProviderLimit
PROVIDER_LIMITS = rules.PROVIDER_LIMITS
DEFAULT_SYNC_MIN = rules.DEFAULT_SYNC_MIN
backfill_start = rules.backfill_start
next_sync_at = rules.next_sync_at
status_after = rules.status_after
# What calendar (P1-09) still writes, and what each reads as since P3-02.
LEGACY_STATUS: Final[Mapping[str, ConnectionStatus]] = {
    "needs_reauth": ConnectionStatus.auth_required,
    "error": ConnectionStatus.degraded,
}


async def upsert_connection(
    ctx: WorkspaceContext,
    *,
    kind: ConnectorKind,
    provider: str,
    account: str,
    status: str = "ok",
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
    status: str,
    *,
    last_error: str | None = None,
    last_sync_at: datetime | None = None,
    detail: str | None = None,
    session: AsyncSession | None = None,
) -> None:
    """Sets the status (a `ConnectionStatus`, or calendar's legacy `needs_reauth` and
    `error`, read back as `auth_required` and `degraded`) and the sentence Settings shows
    with it (`detail`)."""
    if status not in LEGACY_STATUS:
        status = ConnectionStatus(status).value  # ValueError for anything else
    values: dict[str, Any] = {"status": status, "last_error": last_error, "status_detail": detail}
    if last_sync_at is not None:
        values["last_sync_at"] = last_sync_at
    async with session_for(ctx, session) as s:
        await s.execute(
            update(_connections).where(_live_connection(connection_id)).values(**values)
        )


async def get_sync_cursor(
    ctx: WorkspaceContext,
    connection_id: UUID,
    *,
    scope: str = DEFAULT_SCOPE,
    session: AsyncSession | None = None,
) -> dict[str, Any] | None:
    """Where the connection's sync of `scope` is (None: no sync in progress)."""
    async with session_for(ctx, session) as s:
        cursor: dict[str, Any] | None = await s.scalar(
            select(_sync_state.c.cursor).where(
                _sync_state.c.connection_id == connection_id, _sync_state.c.scope == scope
            )
        )
    return cursor


async def save_sync_cursor(
    ctx: WorkspaceContext,
    connection_id: UUID,
    cursor: Mapping[str, Any] | None,
    *,
    at: datetime | None = None,
    items: int = 0,
    scope: str = DEFAULT_SCOPE,
    page_no: int | None = None,
    session: AsyncSession | None = None,
) -> None:
    """Store the cursor (one `sync_state` row per connection and scope, P3-02) in the
    caller's transaction, with the page's records, so a crash resumes after the last
    committed page. `page_no` (P3-02) is how many pages of the running sync are stored."""
    async with session_for(ctx, session) as s:
        insert = pg_insert(_sync_state).values(
            connection_id=connection_id,
            scope=scope,
            cursor=None if cursor is None else dict(cursor),
            last_page_at=at,
            items_seen=items,
            page_no=page_no or 0,
        )
        set_: dict[str, Any] = {
            "cursor": insert.excluded.cursor,
            "last_page_at": func.coalesce(insert.excluded.last_page_at, _sync_state.c.last_page_at),
            "items_seen": _sync_state.c.items_seen + insert.excluded.items_seen,
        }
        if page_no is not None:
            set_["page_no"] = insert.excluded.page_no
        await s.execute(
            insert.on_conflict_do_update(
                index_elements=[
                    _sync_state.c.workspace_id,
                    _sync_state.c.connection_id,
                    _sync_state.c.scope,
                ],
                set_=set_,
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


# --- Connections and the sync framework (P3-02) ----------------------------------------------
#
# A connection is one account of one provider. Its grant (tokens, the registered OAuth
# client and the discovered authorization server) is one blob sealed with the workspace
# data key into `credentials_enc` (Data flow rules 1 and 5): nothing the api answers
# carries it. The `connect_oauth` workflow fills it; `access_token` refreshes it under the
# connection row's lock; `connector_sync` reads pages through `fetch_page` (the provider's
# request limit) and stores each with `persist_page` (records, cursor and one
# `items.ingested`, in one transaction).


class ReauthRequired(Exception):  # noqa: N818  # the plan's name
    """The provider no longer accepts the grant (a refused refresh, an HTTP 401): the
    connection goes to `auth_required` and the user signs in again."""

    def __init__(self, provider: str, message: str) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.message = message


class UnknownProvider(ValueError):  # noqa: N818  # reads as the condition it reports
    """No connector framework provider of this name is registered."""

    def __init__(self, provider: str) -> None:
        super().__init__(f"no provider {provider!r} is registered")
        self.provider = provider


AuthKind = Literal["oauth", "none"]


@dataclass(frozen=True)
class ProviderSpec:
    """A provider the framework connects and syncs (`register_provider`): its connector
    kind, how it signs in, the MCP server it talks to, how far back it keeps data (Granola
    Basic: 30 days) and the notice the user acknowledges before connecting."""

    provider: str
    kind: ConnectorKind
    label: str
    auth: AuthKind = "oauth"
    server_url: str | None = None
    backfill_cap_days: int | None = None
    consent_notice: str | None = None
    fake_only: bool = False  # listed only when TUMNIS_ADAPTERS=fake


_PROVIDERS: dict[str, ProviderSpec] = {}


def register_provider(spec: ProviderSpec) -> None:
    """Registers a provider with the sync framework (its connector registers separately
    through `register_connector`); the same name again replaces it."""
    _PROVIDERS[spec.provider] = spec


def provider_spec(provider: str) -> ProviderSpec:
    spec = _PROVIDERS.get(provider)
    if spec is None:
        raise UnknownProvider(provider)
    return spec


class ProviderOut(BaseModel):
    provider: str
    kind: ConnectorKind
    label: str
    auth: AuthKind
    backfill_cap_days: int | None
    consent_notice: str | None
    sync_every_min: int


def list_providers() -> list[ProviderOut]:
    """The providers a user can connect, by label (the fake one only with fakes)."""
    fake = current_mode() == "fake"
    return [
        ProviderOut(
            provider=spec.provider,
            kind=spec.kind,
            label=spec.label,
            auth=spec.auth,
            backfill_cap_days=spec.backfill_cap_days,
            consent_notice=spec.consent_notice,
            sync_every_min=rules.sync_every(spec.provider, ConnectionSettings()),
        )
        for spec in sorted(_PROVIDERS.values(), key=lambda s: s.label)
        if fake or not spec.fake_only
    ]


AccountLabel = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class ConnectionOut(BaseModel):
    """A connection as the api answers it: never its credentials (Data flow rule 5)."""

    id: UUID
    kind: ConnectorKind
    provider: str
    account_label: str
    status: ConnectionStatus
    status_detail: str | None
    last_success_at: datetime | None
    next_sync_at: datetime | None
    settings: ConnectionSettings
    version: int


class ConnectionCreate(BaseModel):
    provider: Annotated[str, StringConstraints(min_length=1, max_length=60)]
    account_label: AccountLabel
    settings: ConnectionSettings = Field(default_factory=ConnectionSettings)
    consent_acknowledged: bool = False  # the provider's consent notice was shown and accepted


class ConnectionPatch(BaseModel):
    account_label: AccountLabel | None = None
    settings: ConnectionSettings | None = None
    version: Version


class DisconnectIn(BaseModel):
    reason: Reason


class ConnectionsOAuthStart(BaseModel):
    """`POST /v1/connections/{id}/oauth/start`: the `connect_oauth` workflow to poll."""

    workflow_id: str


class AuthorizeUrlOut(BaseModel):
    """`GET /v1/connections/{id}/oauth/url`: the provider's sign-in page once the
    workflow has prepared it, else null (poll again)."""

    authorize_url: str | None


class ConsentRequired(ValueError):  # noqa: N818  # reads as the condition it reports
    """The provider shows a consent notice (Granola: meeting recording) that was not
    acknowledged."""


def _status(value: str) -> ConnectionStatus:
    return LEGACY_STATUS.get(value) or ConnectionStatus(value)


def _connection_out(row: Any) -> ConnectionOut:
    return ConnectionOut(
        id=row.id,
        kind=row.kind,
        provider=row.provider,
        account_label=row.account_label or row.account,
        status=_status(row.status),
        status_detail=row.status_detail,
        last_success_at=row.last_success_at,
        next_sync_at=row.next_sync_at,
        settings=ConnectionSettings.model_validate(row.settings or {}),
        version=row.version,
    )


def _framework() -> ColumnElement[bool]:
    """The connections the framework owns: those of a registered provider (calendar's
    Google accounts, github's and the seed's are their modules')."""
    return _connections.c.provider.in_(sorted(_PROVIDERS))


async def _row(s: AsyncSession, connection_id: UUID, *, lock: bool = False) -> Any:
    stmt = select(_connections).where(_live_connection(connection_id), _framework())
    if lock:
        stmt = stmt.with_for_update()
    row = (await s.execute(stmt)).first()
    if row is None:
        raise NotFound("connections", connection_id)
    return row


async def create_connection(
    ctx: WorkspaceContext,
    provider: str,
    settings: ConnectionSettings,
    *,
    account_label: str,
    consent_acknowledged: bool = False,
    now: datetime | None = None,
    session: AsyncSession | None = None,
) -> ConnectionOut:
    """A new connection of `provider`, `pending_auth` until its OAuth completes. Its
    `account` is a placeholder (its own id) until the provider names the account (an
    assumption P3-01 checks: docs/plan/p3-02-provider-assumptions.md). UnknownProvider for
    an unregistered provider; ConsentRequired when its notice was not acknowledged."""
    spec = provider_spec(provider)
    if spec.consent_notice and not consent_acknowledged:
        raise ConsentRequired(f"{spec.label} needs its consent notice acknowledged")
    connection_id = uuid7()
    async with session_for(ctx, session) as s:
        await s.execute(
            pg_insert(_connections).values(
                id=connection_id,
                kind=spec.kind,
                provider=provider,
                account=str(connection_id),
                account_label=account_label,
                settings=settings.model_dump(mode="json"),
                status=ConnectionStatus.pending_auth.value,
                consent_ack_at=(now or func.now()) if consent_acknowledged else None,
            )
        )
        return _connection_out(await _row(s, connection_id))


async def get_connection(
    ctx: WorkspaceContext, connection_id: UUID, *, session: AsyncSession | None = None
) -> ConnectionOut:
    """NotFound when it is not a live framework connection of the workspace."""
    async with session_for(ctx, session) as s:
        return _connection_out(await _row(s, connection_id))


async def list_connections(
    ctx: WorkspaceContext, *, session: AsyncSession | None = None
) -> list[ConnectionOut]:
    """The workspace's live connections, oldest first."""
    async with session_for(ctx, session) as s:
        rows = await s.execute(
            select(_connections)
            .where(_connections.c.deleted_at.is_(None), _framework())
            .order_by(_connections.c.created_at, _connections.c.id)
        )
        return [_connection_out(row) for row in rows]


async def update_connection(
    ctx: WorkspaceContext,
    connection_id: UUID,
    patch: ConnectionPatch,
    *,
    session: AsyncSession | None = None,
) -> ConnectionOut:
    """Renames the account or changes its settings at the version read (StaleVersion
    carries the connection as the api shows it, never the row with its credentials)."""
    values: dict[str, Any] = {}
    if patch.account_label is not None:
        values["account_label"] = patch.account_label
    if patch.settings is not None:
        values["settings"] = patch.settings.model_dump(mode="json")
    async with session_for(ctx, session) as s:
        current = await _row(s, connection_id)
        if current.version != patch.version:
            raise StaleVersion(current=_connection_out(current).model_dump(mode="json"))
        if not values:
            return _connection_out(current)
        try:
            await update_versioned(s, _connections, connection_id, patch.version, values)
        except StaleVersion:
            raise StaleVersion(
                current=_connection_out(await _row(s, connection_id)).model_dump(mode="json")
            ) from None
        return _connection_out(await _row(s, connection_id))


async def disconnect(
    ctx: WorkspaceContext,
    connection_id: UUID,
    reason: str,
    *,
    now: datetime,
    session: AsyncSession | None = None,
) -> None:
    """Disconnects for good: credentials dropped, status `disabled`, the row soft-deleted
    and `connector.disconnected` audited with the reason, in one transaction. What it
    synced stays until a purge (P3-09)."""
    async with session_for(ctx, session) as s:
        row = await _row(s, connection_id, lock=True)
        await s.execute(
            update(_connections)
            .where(_connections.c.id == connection_id)
            .values(
                credentials_enc=None,
                key_version=None,
                status=ConnectionStatus.disabled.value,
                status_detail=None,
                next_sync_at=None,
                deleted_at=now,
            )
        )
        await audit.record(
            s,
            "connector.disconnected",
            target=("connection", connection_id),
            reason=reason,
            details={"provider": row.provider, "kind": row.kind},
            occurred_at=now,
        )


async def set_next_sync_at(
    ctx: WorkspaceContext,
    connection_id: UUID,
    at: datetime | None,
    *,
    session: AsyncSession | None = None,
) -> None:
    """When the connection is next due; None: due now (Sync now)."""
    async with session_for(ctx, session) as s:
        await s.execute(
            update(_connections).where(_live_connection(connection_id)).values(next_sync_at=at)
        )


# Grants: one sealed blob {"tokens": {..., "expires_at"}, "client_info": {...},
# "server": {...}} per connection.

REFRESH_MARGIN: Final = timedelta(seconds=60)  # plan default: refresh a minute early


async def _blob(s: AsyncSession, ctx: WorkspaceContext, connection_id: UUID) -> dict[str, Any]:
    return await get_credentials(ctx, connection_id, session=s) or {}


async def _update_blob(
    ctx: WorkspaceContext,
    connection_id: UUID,
    changes: Mapping[str, Any],
    *,
    session: AsyncSession | None = None,
) -> None:
    async with session_for(ctx, session) as s:
        await s.execute(
            select(_connections.c.id).where(_live_connection(connection_id)).with_for_update()
        )
        blob = await _blob(s, ctx, connection_id)
        await put_credentials(ctx, connection_id, {**blob, **changes}, session=s)


def _tokens_out(stored: Mapping[str, Any] | None) -> OAuthToken | None:
    if not stored:
        return None
    return OAuthToken.model_validate({k: v for k, v in stored.items() if k != "expires_at"})


def _tokens_in(tokens: OAuthToken, now: datetime) -> dict[str, Any]:
    stored = tokens.model_dump(mode="json", exclude_none=True)
    if tokens.expires_in is not None:
        stored["expires_at"] = (now + timedelta(seconds=tokens.expires_in)).isoformat()
    return stored


class ConnectionTokenStorage:
    """The MCP SDK's `TokenStorage` over the connection's sealed grant: tokens (with the
    time they expire, which the SDK's `OAuthToken` lacks) and the registered client.
    Losing the client info would force a new registration and a new consent, so it is
    kept with the tokens."""

    def __init__(self, ctx: WorkspaceContext, connection_id: UUID, *, clock: Clock) -> None:
        self.ctx = ctx
        self.connection_id = connection_id
        self.clock = clock

    async def get_tokens(self) -> OAuthToken | None:
        async with tenant_session(self.ctx) as s:
            return _tokens_out((await _blob(s, self.ctx, self.connection_id)).get("tokens"))

    async def set_tokens(self, tokens: OAuthToken) -> None:
        await _update_blob(
            self.ctx, self.connection_id, {"tokens": _tokens_in(tokens, self.clock.now())}
        )

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        async with tenant_session(self.ctx) as s:
            info = (await _blob(s, self.ctx, self.connection_id)).get("client_info")
        return None if info is None else OAuthClientInformationFull.model_validate(info)

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        await _update_blob(
            self.ctx,
            self.connection_id,
            {"client_info": client_info.model_dump(mode="json", exclude_none=True)},
        )


async def store_oauth_server(
    ctx: WorkspaceContext,
    connection_id: UUID,
    server: OAuthServer,
    *,
    session: AsyncSession | None = None,
) -> None:
    """Keeps what discovery found with the grant, so a refresh never discovers again."""
    await _update_blob(
        ctx, connection_id, {"server": server.model_dump(mode="json")}, session=session
    )


async def oauth_server_of(
    ctx: WorkspaceContext, connection_id: UUID, *, session: AsyncSession | None = None
) -> OAuthServer | None:
    async with session_for(ctx, session) as s:
        stored = (await _blob(s, ctx, connection_id)).get("server")
    return None if stored is None else OAuthServer.model_validate(stored)


async def access_token(
    ctx: WorkspaceContext, connection_id: UUID, *, oauth: OAuthPort, clock: Clock
) -> str:
    """A live access token. One expiring within REFRESH_MARGIN is refreshed while the
    connection row is locked (SELECT ... FOR UPDATE), and the new tokens (a rotated
    refresh token included) are stored in that same transaction before anyone uses them:
    a second caller waits for the lock, then finds the fresh token. A refused refresh, or
    a grant with nothing to refresh with, raises ReauthRequired."""
    async with tenant_session(ctx) as s:
        row = await _row(s, connection_id, lock=True)
        blob = await _blob(s, ctx, connection_id)
        tokens = blob.get("tokens") or {}
        if not tokens.get("access_token"):
            raise ReauthRequired(row.provider, "no grant")
        expires_at = tokens.get("expires_at")
        now = clock.now()
        if expires_at is None or datetime.fromisoformat(expires_at) - now > REFRESH_MARGIN:
            token: str = tokens["access_token"]
            return token
        server, client = blob.get("server"), blob.get("client_info")
        refresh_token = tokens.get("refresh_token")
        if server is None or client is None or not refresh_token:
            raise ReauthRequired(row.provider, "the grant cannot be refreshed")
        try:
            fresh = await oauth.refresh(
                OAuthServer.model_validate(server),
                OAuthClientInformationFull.model_validate(client),
                refresh_token=refresh_token,
            )
        except OAuthRefused as exc:
            raise ReauthRequired(row.provider, exc.error) from None
        if fresh.refresh_token is None:  # RFC 6749 section 6: the old one stays valid
            fresh = fresh.model_copy(update={"refresh_token": refresh_token})
        await put_credentials(
            ctx, connection_id, {**blob, "tokens": _tokens_in(fresh, now)}, session=s
        )
        return fresh.access_token


# Paging: the provider's request limit, then one page.

_windows = SlidingWindows()


def provider_limit_key(provider: str, account: str) -> str:
    """Limits hold per provider account, not per workspace (plan)."""
    return f"provider:{provider}:{account}"


async def fetch_page(
    ctx: WorkspaceContext,
    connection_id: UUID,
    cursor: dict[str, Any] | None,
    *,
    connector: Connector,
    clock: Clock,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> SyncPage:
    """One page from the connector, once the provider account's limit
    (`rules.PROVIDER_LIMITS`) has room: a caller over the limit waits (never fails) until
    the oldest request in the window leaves it. The admission and the request happen with
    no await between them, so the window counts request starts."""
    async with tenant_session(ctx) as s:
        row = await _row(s, connection_id)
    limit = rules.provider_limit(row.provider)
    key = provider_limit_key(row.provider, row.account)
    while (wait := _windows.admit(key, limit.requests, limit.period_s, clock.now())) is not None:
        await sleep(wait)
    return await connector.sync(cursor)


class ItemsIngestedOut(BaseModel):
    """What one stored page changed."""

    item_ids: list[UUID]
    more: bool


async def persist_page(
    ctx: WorkspaceContext,
    connection_id: UUID,
    connector: Connector,
    page: SyncPage,
    *,
    scope: str,
    page_no: int,
    at: datetime,
    session: AsyncSession,
) -> ItemsIngestedOut:
    """In the caller's transaction: the page's raw payloads and records (`ingest_page`),
    its tombstones, the next cursor of `scope` with the page count, and one
    `items.ingested{connection_id, item_ids}` when records changed. The cursor of a last
    page (no more) is kept as the connector's resume point; with none, the scope starts
    from its last success next time. Logs nothing of the payloads (SEC-6)."""
    result = await ingest_page(ctx, connection_id, connector, page, session=session)
    following = page.next_cursor if (page.has_more or page.next_cursor) else None
    await save_sync_cursor(
        ctx,
        connection_id,
        following,
        at=at,
        items=len(page.items),
        scope=scope,
        page_no=page_no,
        session=session,
    )
    if result.changed_ids:
        await emit(
            session,
            ItemsIngestedV1(connection_id=connection_id, item_ids=result.changed_ids),
            occurred_at=at,
        )
    return ItemsIngestedOut(item_ids=result.changed_ids, more=page.has_more)


class SyncStart(BaseModel):
    """`begin_sync`'s answer: `ready` with the scopes to page through, or why not."""

    status: Literal["ready", "skipped"]
    provider: str = ""
    scopes: list[str] = Field(default_factory=list)


def _first_cursor(scope: str, since: datetime) -> dict[str, Any]:
    return {"schema_version": 1, "scope": scope, "since": since.isoformat()}


async def begin_sync(
    ctx: WorkspaceContext, connection_id: UUID, scopes: Sequence[str], *, now: datetime
) -> SyncStart:
    """A sync starts: the connection (only `ok` or `degraded` ones sync) goes `syncing`,
    and every scope's page count restarts. A scope with no cursor starts at its backfill
    window (first sync, Data flow rule 3) or at the last success."""
    async with tenant_session(ctx) as s:
        row = await _row(s, connection_id, lock=True)
        if _status(row.status) not in {ConnectionStatus.ok, ConnectionStatus.degraded}:
            return SyncStart(status="skipped", provider=row.provider)
        settings = ConnectionSettings.model_validate(row.settings or {})
        cap = _PROVIDERS[row.provider].backfill_cap_days
        since = row.last_success_at or rules.backfill_start(now, settings, cap)
        for scope in scopes:
            stored = await s.execute(
                select(_sync_state.c.cursor).where(
                    _sync_state.c.connection_id == connection_id, _sync_state.c.scope == scope
                )
            )
            cursor = stored.scalar_one_or_none()
            await save_sync_cursor(
                ctx,
                connection_id,
                cursor or _first_cursor(scope, since),
                scope=scope,
                page_no=0,
                session=s,
            )
        await s.execute(
            update(_connections)
            .where(_connections.c.id == connection_id)
            .values(status=ConnectionStatus.syncing.value)
        )
    return SyncStart(status="ready", provider=row.provider, scopes=list(scopes))


async def sync_position(
    ctx: WorkspaceContext, connection_id: UUID, scope: str
) -> tuple[dict[str, Any] | None, int]:
    """The stored cursor of `scope` and how many pages of the running sync are stored."""
    async with tenant_session(ctx) as s:
        row = (
            await s.execute(
                select(_sync_state.c.cursor, _sync_state.c.page_no).where(
                    _sync_state.c.connection_id == connection_id, _sync_state.c.scope == scope
                )
            )
        ).first()
    return (None, 0) if row is None else (row.cursor, row.page_no)


DETAILS: Final[Mapping[SyncOutcome, str | None]] = {
    SyncOutcome.success: None,
    SyncOutcome.transient_error: "Server error from provider, retrying",
    SyncOutcome.auth_error: "Sign in again",
}


async def finish_sync(
    ctx: WorkspaceContext,
    connection_id: UUID,
    outcome: SyncOutcome,
    *,
    now: datetime,
    jitter_s: float = 0,
) -> ConnectionOut:
    """A sync ended: the status the table gives (`rules.status_after`), the sentence
    Settings shows, the failures in a row and the next sync (backoff after a failure).
    Turning `auth_required` emits `connection.auth_required` (tasks queues a review item)
    in the same transaction."""
    async with tenant_session(ctx) as s:
        row = await _row(s, connection_id, lock=True)
        prev = _status(row.status)
        status = rules.status_after(prev, outcome)
        settings = ConnectionSettings.model_validate(row.settings or {})
        every = rules.sync_every(row.provider, settings)
        success = outcome == SyncOutcome.success
        failures = 0 if success else row.failures + 1
        values: dict[str, Any] = {
            "status": status.value,
            "status_detail": DETAILS[outcome],
            "failures": failures,
            "last_error": None if success else DETAILS[outcome],
            "next_sync_at": (
                None
                if status == ConnectionStatus.auth_required
                else rules.next_sync_at(
                    now, now if success else row.last_success_at, every, failures, jitter_s
                )
            ),
        }
        if success:
            values |= {"last_success_at": now, "last_sync_at": now}
        await s.execute(
            update(_connections).where(_connections.c.id == connection_id).values(**values)
        )
        if status == ConnectionStatus.auth_required and prev != ConnectionStatus.auth_required:
            await emit(
                s,
                ConnectionAuthRequiredV1(
                    connection_id=connection_id,
                    provider=row.provider,
                    account_label=row.account_label or row.account,
                ),
                occurred_at=now,
            )
        return _connection_out(await _row(s, connection_id))


async def due_connections(ctx: WorkspaceContext, *, now: datetime) -> list[UUID]:
    """The workspace's connections a sync tick enqueues: `ok` or `degraded`, of a
    registered provider, and due (`next_sync_at` unset or reached)."""
    async with tenant_session(ctx) as s:
        rows: ScalarResult[UUID] = await s.scalars(
            select(_connections.c.id)
            .where(
                _connections.c.deleted_at.is_(None),
                _framework(),
                _connections.c.status.in_(
                    [ConnectionStatus.ok.value, ConnectionStatus.degraded.value]
                ),
                or_(_connections.c.next_sync_at.is_(None), _connections.c.next_sync_at <= now),
            )
            .order_by(_connections.c.next_sync_at.nulls_first(), _connections.c.id)
        )
        return list(rows)


# OAuth: the worker's half (`connect_oauth`) and the callback's.


class PreparedOAuth(BaseModel):
    """What the prepare step leaves for the exchange: the pending consent's id and the
    URL the user signs in at."""

    pending_id: UUID
    authorize_url: str


async def prepare_oauth(
    ctx: WorkspaceContext,
    connection_id: UUID,
    *,
    oauth: OAuthPort,
    redirect_uri: str,
    workflow_id: str,
    now: datetime,
) -> PreparedOAuth:
    """Discovery (once), dynamic client registration (once per connection: the client
    is kept with the grant), then a consent in flight: a random `state` (only its hash
    kept) and a PKCE verifier (sealed), valid for OAUTH_PENDING_TTL, tied to this
    connection and the waiting workflow."""
    async with tenant_session(ctx) as s:
        row = await _row(s, connection_id)
    spec = provider_spec(row.provider)
    if spec.server_url is None:
        raise ValueError(f"{spec.provider} has no MCP server to sign in at")
    storage = ConnectionTokenStorage(ctx, connection_id, clock=_NoClock())
    server = await oauth_server_of(ctx, connection_id)
    if server is None:
        server = await oauth.discover(spec.server_url)
        await store_oauth_server(ctx, connection_id, server)
    client = await storage.get_client_info()
    if client is None or redirect_uri not in {str(u) for u in client.redirect_uris or []}:
        client = await oauth.register(server, client_metadata(redirect_uri, server))
        await storage.set_client_info(client)
    started = await begin_oauth(ctx, row.provider, redirect_uri=redirect_uri, now=now)
    async with tenant_session(ctx) as s:
        await s.execute(
            update(_pending)
            .where(_pending.c.id == started.pending_id)
            .values(connection_id=connection_id, workflow_id=workflow_id)
        )
    query = {
        "response_type": "code",
        "client_id": client.client_id or "",
        "redirect_uri": redirect_uri,
        "state": started.state,
        "code_challenge": started.code_challenge,
        "code_challenge_method": "S256",
        "resource": server.resource,
    }
    if server.scopes:
        query["scope"] = " ".join(server.scopes)
    return PreparedOAuth(
        pending_id=started.pending_id,
        authorize_url=f"{server.authorization_endpoint}?{urlencode(query)}",
    )


def client_metadata(redirect_uri: str, server: OAuthServer) -> OAuthClientMetadata:
    """How Tumnis registers with a provider (RFC 7591): a public client using the
    authorization code with PKCE and refresh tokens, no client secret."""
    return OAuthClientMetadata(
        client_name="Tumnis Guide",
        redirect_uris=[AnyUrl(redirect_uri)],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",  # noqa: S106  # a public client: PKCE, no secret
        scope=" ".join(server.scopes) or None,
    )


class _NoClock:
    """Client info is stored without an expiry; ConnectionTokenStorage needs a clock only
    for tokens."""

    def now(self) -> datetime:  # pragma: no cover  # never called for client info
        raise RuntimeError("no clock")


class CallbackAccepted(BaseModel):
    """What the browser callback found: `accepted` (code stored, workflow told), or
    `mismatch` (no consent of this workspace has that state, or it is used or expired)."""

    outcome: Literal["accepted", "declined", "mismatch"]
    connection_id: UUID | None = None
    workflow_id: str | None = None
    pending_id: UUID | None = None


async def accept_connection_callback(
    ctx: WorkspaceContext,
    *,
    state: str,
    code: str | None,
    iss: str | None,
    now: datetime,
) -> CallbackAccepted:
    """The callback's half (no outbound call, no wait): the consent `state` names, if it
    is a connection's, unexpired and unused, is used up and its code stored sealed with
    the issuer the provider reported. A stale or unknown state is a mismatch."""
    async with tenant_session(ctx) as s:
        found = (
            await s.execute(
                select(_pending.c.connection_id, _pending.c.workflow_id, _pending.c.provider)
                .where(
                    _pending.c.state_hash == _state_hash(state),
                    _pending.c.connection_id.is_not(None),
                )
                .with_for_update()
            )
        ).first()
        if found is None:
            return CallbackAccepted(outcome="mismatch")
        accepted = await accept_oauth_code(
            ctx, found.provider, state=state, code=code, now=now, session=s
        )
        if accepted.outcome in {"unknown", "invalid"}:
            return CallbackAccepted(outcome="mismatch", connection_id=found.connection_id)
        if iss is not None:
            await s.execute(
                update(_pending).where(_pending.c.id == accepted.pending_id).values(iss=iss)
            )
    return CallbackAccepted(
        outcome="accepted" if accepted.outcome == "accepted" else "declined",
        connection_id=found.connection_id,
        workflow_id=found.workflow_id,
        pending_id=accepted.pending_id,
    )


async def audit_state_mismatch(ctx: WorkspaceContext, *, now: datetime) -> None:
    """`connector.oauth_state_mismatch`, in its own committed transaction (the request
    that found it answers 400)."""
    async with tenant_session(ctx) as s:
        await audit.record(s, "connector.oauth_state_mismatch", occurred_at=now)


async def complete_oauth(
    ctx: WorkspaceContext,
    connection_id: UUID,
    pending_id: UUID,
    *,
    oauth: OAuthPort,
    clock: Clock,
    actor: str,
) -> ConnectionStatus:
    """Exchanges the stored code with the verifier, stores the tokens sealed, consumes
    the consent and marks the connection `ok` (due now), auditing `connector.connected`
    as `actor` (the user who started it), all but the exchange in one transaction. A code
    the server refuses leaves the connection `pending_auth` (the user connects again)."""
    grant = await read_oauth_grant(ctx, pending_id)
    if grant is None:
        return _status((await get_connection(ctx, connection_id)).status)
    server = await oauth_server_of(ctx, connection_id)
    client = await ConnectionTokenStorage(ctx, connection_id, clock=clock).get_client_info()
    if server is None or client is None:
        raise ReauthRequired("oauth", "no registration to exchange the code with")
    try:
        tokens = await oauth.exchange(
            server,
            client,
            code=grant.code,
            code_verifier=grant.code_verifier,
            redirect_uri=grant.redirect_uri,
        )
    except OAuthRefused:
        await consume_oauth_grant(ctx, pending_id)
        return ConnectionStatus.pending_auth
    now = clock.now()
    async with tenant_session(WorkspaceContext(ctx.workspace_id, ActorRef(actor))) as s:
        row = await _row(s, connection_id, lock=True)
        blob = await _blob(s, ctx, connection_id)
        await put_credentials(
            ctx, connection_id, {**blob, "tokens": _tokens_in(tokens, now)}, session=s
        )
        await consume_oauth_grant(ctx, pending_id, session=s)
        await s.execute(
            update(_connections)
            .where(_connections.c.id == connection_id)
            .values(
                status=ConnectionStatus.ok.value,
                status_detail=None,
                failures=0,
                next_sync_at=None,
            )
        )
        await audit.record(
            s,
            "connector.connected",
            target=("connection", connection_id),
            details={"provider": row.provider, "kind": row.kind},
            occurred_at=now,
        )
    return ConnectionStatus.ok


async def expire_oauth(ctx: WorkspaceContext, connection_id: UUID) -> ConnectionStatus:
    """Nobody came back from the sign-in page in time: a connection never connected stays
    `pending_auth`; one reconnecting keeps its status."""
    return (await get_connection(ctx, connection_id)).status


# Starting work in the worker: the api only enqueues (it never calls out, principle 3).

SYNC_QUEUE: Final = "sync"  # worker.SYNC_QUEUE
SYNC_WORKFLOW: Final = "integrations_connector_sync"
CONNECT_WORKFLOW: Final = "integrations_connect_oauth"
TICK_WORKFLOW: Final = "integrations_connector_sync_tick"
AUTHORIZE_URL_EVENT: Final = "authorize_url"
OAUTH_TOPIC: Final = "oauth_callback"
CALLBACK_PATH: Final = "/v1/connections/oauth/callback"


def sync_dedup_id(connection_id: UUID | str) -> str:
    """One queued or running sync per connection: a tick and Sync now share it."""
    return f"sync:{connection_id}"


async def start_oauth(
    ctx: WorkspaceContext, connection_id: UUID, *, base_url: str
) -> ConnectionsOAuthStart:
    """Enqueues `connect_oauth` for the connection (signing in, or again after
    `auth_required`) with the callback URL and the user who started it (the actor of
    `connector.connected`); the UI polls `authorize_url` for the sign-in page."""
    connection = await get_connection(ctx, connection_id)
    if connection.status == ConnectionStatus.disabled:
        raise NotFound("connections", connection_id)
    workflow_id = f"connect-oauth:{connection_id}:{uuid7()}"
    await deadletter.dbos_client().enqueue_async(
        {"queue_name": SYNC_QUEUE, "workflow_name": CONNECT_WORKFLOW, "workflow_id": workflow_id},
        str(ctx.workspace_id),
        str(connection_id),
        base_url.rstrip("/") + CALLBACK_PATH,
        str(ctx.actor),
    )
    return ConnectionsOAuthStart(workflow_id=workflow_id)


async def authorize_url(
    ctx: WorkspaceContext, connection_id: UUID, *, now: datetime
) -> AuthorizeUrlOut:
    """The sign-in page of the connection's latest consent still in flight, once its
    workflow has published it; null before that (never waits, R-30)."""
    async with tenant_session(ctx) as s:
        await _row(s, connection_id)
        workflow_id: str | None = await s.scalar(
            select(_pending.c.workflow_id)
            .where(
                _pending.c.connection_id == connection_id,
                _pending.c.used_at.is_(None),
                _pending.c.deleted_at.is_(None),
                _pending.c.expires_at > now,
            )
            .order_by(_pending.c.created_at.desc(), _pending.c.id.desc())
            .limit(1)
        )
    if workflow_id is None:
        return AuthorizeUrlOut(authorize_url=None)
    url = await deadletter.dbos_client().get_event_async(
        workflow_id, AUTHORIZE_URL_EVENT, timeout_seconds=0
    )
    return AuthorizeUrlOut(authorize_url=url if isinstance(url, str) else None)


async def deliver_callback(accepted: CallbackAccepted) -> None:
    """Tells the waiting `connect_oauth` that its code arrived (or that the user said no).
    A workflow that is gone (it timed out) is left alone: the user connects again."""
    if accepted.workflow_id is None or accepted.pending_id is None:
        return
    with contextlib.suppress(Exception):
        await deadletter.dbos_client().send_async(
            accepted.workflow_id,
            {"pending_id": str(accepted.pending_id), "outcome": accepted.outcome},
            OAUTH_TOPIC,
        )


async def request_sync(ctx: WorkspaceContext, connection_id: UUID) -> ConnectionOut:
    """Sync now: due now, and one sync enqueued (or the one already queued or running)."""
    connection = await get_connection(ctx, connection_id)
    await set_next_sync_at(ctx, connection_id, None)
    await deadletter.dbos_client().enqueue_async(
        {
            "queue_name": SYNC_QUEUE,
            "workflow_name": SYNC_WORKFLOW,
            "deduplication_id": sync_dedup_id(connection_id),
            "duplication_policy": "return-existing",
        },
        str(ctx.workspace_id),
        str(connection_id),
    )
    return connection


# Metrics: the sync age and items seen per provider, read on every /metrics scrape.

_SYNC_AGES_SQL = text("SELECT provider, age_seconds, items FROM app.connector_sync_ages()")


async def export_metrics(conn: AsyncConnection) -> None:
    """`tumnis_connector_sync_age_seconds{provider}` (the oldest live connection's time
    since its last good sync, or since it was made) and
    `tumnis_connector_items_total{provider}` (items its syncs have read). Only the
    framework's providers: calendar's and the seed's connections sync in their own modules
    and never set `last_success_at`."""
    rows = [row for row in (await conn.execute(_SYNC_AGES_SQL)).all() if row.provider in _PROVIDERS]
    CONNECTOR_SYNC_AGE.set_all({row.provider: float(row.age_seconds) for row in rows})
    CONNECTOR_ITEMS.set_all({row.provider: float(row.items) for row in rows})


# --- Artifacts: status kept fresh by the modules that read the outside system (P2-13) ---------

_artifacts: Table = _TABLES["artifact"]


class ArtifactOut(BaseModel):
    id: UUID
    connection_id: UUID
    kind: str
    external_id: str
    url: str | None
    state: str | None
    checks: dict[str, Any]
    fetched_at: datetime


def _artifact_out(row: Any) -> ArtifactOut:
    return ArtifactOut.model_validate(row._mapping)


async def upsert_artifact(  # the plan's signature
    ctx: WorkspaceContext,
    *,
    connection_id: UUID,
    kind: str,
    external_id: str,
    url: str | None,
    now: datetime,
    session: AsyncSession | None = None,
) -> ArtifactOut:
    """The artifact for (connection, external id), created with no state (nothing has read
    it yet) or, when it exists, left as it is (a soft-deleted one comes back). The status
    arrives later through `set_artifact_status`."""
    record = ArtifactRecord(
        external_id=external_id, provider_url=url, fetched_at=now, kind=kind, url=url
    )
    async with session_for(ctx, session) as s:
        source = await connection_source(s, connection_id)
        insert = pg_insert(_artifacts).values(
            connection_id=connection_id,
            external_id=external_id,
            provider_url=url,
            fetched_at=now,
            content_hash=content_hash(record),
            source=source,
            kind=kind,
            url=url,
            state=None,
            checks={},
        )
        await s.execute(
            insert.on_conflict_do_update(
                index_elements=[_artifacts.c[c] for c in CANONICAL_KEY],
                set_={"deleted_at": None},
                where=_artifacts.c.deleted_at.is_not(None),
            )
        )
        row = (
            await s.execute(
                select(_artifacts).where(
                    _artifacts.c.connection_id == connection_id,
                    _artifacts.c.external_id == external_id,
                )
            )
        ).one()
    return _artifact_out(row)


async def get_artifacts(
    ctx: WorkspaceContext, ids: Sequence[UUID], *, session: AsyncSession | None = None
) -> list[ArtifactOut]:
    """The live artifacts among `ids`, in the order of `ids`."""
    if not ids:
        return []
    async with session_for(ctx, session) as s:
        rows = (
            await s.execute(
                select(_artifacts).where(
                    _artifacts.c.id.in_(list(ids)), _artifacts.c.deleted_at.is_(None)
                )
            )
        ).all()
    found = {row.id: _artifact_out(row) for row in rows}
    return [found[i] for i in ids if i in found]


async def find_artifacts(
    ctx: WorkspaceContext,
    *,
    kind: str,
    external_ids: Sequence[str],
    session: AsyncSession | None = None,
) -> list[ArtifactOut]:
    """The live artifacts of one kind with these external ids (any connection)."""
    if not external_ids:
        return []
    async with session_for(ctx, session) as s:
        rows = (
            await s.execute(
                select(_artifacts).where(
                    _artifacts.c.kind == kind,
                    _artifacts.c.external_id.in_(list(external_ids)),
                    _artifacts.c.deleted_at.is_(None),
                )
            )
        ).all()
    return [_artifact_out(row) for row in rows]


async def list_artifacts(
    ctx: WorkspaceContext,
    *,
    kind: str,
    open_only: bool = False,
    session: AsyncSession | None = None,
) -> list[ArtifactOut]:
    """The workspace's live artifacts of one kind, oldest read first. `open_only` keeps
    those never read (no state) or still `open`: the ones worth polling."""
    stmt = select(_artifacts).where(_artifacts.c.kind == kind, _artifacts.c.deleted_at.is_(None))
    if open_only:
        stmt = stmt.where(or_(_artifacts.c.state.is_(None), _artifacts.c.state == "open"))
    async with session_for(ctx, session) as s:
        rows = (await s.execute(stmt.order_by(_artifacts.c.fetched_at, _artifacts.c.id))).all()
    return [_artifact_out(row) for row in rows]


async def set_artifact_status(
    ctx: WorkspaceContext,
    artifact_id: UUID,
    *,
    state: str | None,
    checks: Mapping[str, Any],
    fetched_at: datetime,
    session: AsyncSession | None = None,
) -> bool:
    """Stores what the outside system said and when it was read (`fetched_at` moves on
    every read). When the state or the checks differ from what was stored, emits
    `artifact.updated` in the same transaction and returns True; a read that changed
    nothing emits nothing. NotFound for an artifact that is gone."""
    async with session_for(ctx, session) as s:
        row = (
            (
                await s.execute(
                    select(_artifacts)
                    .where(_artifacts.c.id == artifact_id, _artifacts.c.deleted_at.is_(None))
                    .with_for_update()
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound("artifacts", artifact_id)
        new_checks = dict(checks)
        changed = bool(row["state"] != state or row["checks"] != new_checks)
        values: dict[str, Any] = {"fetched_at": fetched_at}
        if changed:
            record = ArtifactRecord(
                external_id=row["external_id"],
                provider_url=row["provider_url"],
                fetched_at=fetched_at,
                kind=row["kind"],
                url=row["url"],
                state=state,
                checks=new_checks,
            )
            values |= {"state": state, "checks": new_checks, "content_hash": content_hash(record)}
        await s.execute(update(_artifacts).where(_artifacts.c.id == artifact_id).values(**values))
        if changed:
            await emit(
                s,
                ArtifactUpdatedV1(
                    artifact_id=artifact_id,
                    kind=row["kind"],
                    url=row["url"],
                    state=state,
                    checks=new_checks,
                ),
                occurred_at=fetched_at,
            )
    return changed


async def get_raw_payload(
    ctx: WorkspaceContext,
    connection_id: UUID,
    record_type: str,
    external_id: str,
    *,
    session: AsyncSession | None = None,
) -> dict[str, Any] | None:
    """The stored provider JSON of one record (what `store_raw_payloads` keeps), or None."""
    async with session_for(ctx, session) as s:
        payload: dict[str, Any] | None = await s.scalar(
            select(_raw.c.payload).where(
                _raw.c.connection_id == connection_id,
                _raw.c.record_type == record_type,
                _raw.c.external_id == external_id,
                _raw.c.deleted_at.is_(None),
            )
        )
    return payload


async def context_owners(
    ctx: WorkspaceContext,
    *,
    target_type: TargetType,
    target_id: UUID,
    owner_type: OwnerType,
    session: AsyncSession | None = None,
) -> list[UUID]:
    """The owners (tasks, say) whose live context items point at this target."""
    async with session_for(ctx, session) as s:
        owners: Sequence[UUID] = (
            await s.scalars(
                select(_context.c.owner_id)
                .where(
                    _context.c.target_type == target_type,
                    _context.c.target_id == target_id,
                    _context.c.owner_type == owner_type,
                    _context.c.deleted_at.is_(None),
                )
                .order_by(_context.c.created_at, _context.c.id)
            )
        ).all()
        return list(owners)


async def context_targets(
    ctx: WorkspaceContext,
    *,
    owner_type: OwnerType,
    owner_id: UUID,
    target_type: TargetType,
    session: AsyncSession | None = None,
) -> list[UUID]:
    """The record ids of one type an owner's live context items point at, oldest first."""
    async with session_for(ctx, session) as s:
        targets: Sequence[UUID | None] = (
            await s.scalars(
                select(_context.c.target_id)
                .where(
                    _context.c.owner_type == owner_type,
                    _context.c.owner_id == owner_id,
                    _context.c.target_type == target_type,
                    _context.c.target_id.is_not(None),
                    _context.c.deleted_at.is_(None),
                )
                .order_by(_context.c.created_at, _context.c.id)
            )
        ).all()
        return [target for target in targets if target is not None]


# --- purge (P2-18, R-37) ----------------------------------------------------------------------


class PurgeIn(BaseModel):
    """`POST /v1/purges`: what to purge and why (the reason goes to the audit row).
    P2-18 purges an archived `project`; P3-09 adds `connection`."""

    scope: Literal["project"]
    id: UUID
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class PurgeOut(BaseModel):
    scope: Literal["project"]
    id: UUID
    status: Literal["accepted"] = "accepted"


async def purge(s: AsyncSession, body: PurgeIn, *, now: datetime) -> PurgeOut:
    """Purge for good, audited as `data.purged` in this transaction; what the purged thing
    kept elsewhere (the agent server's archive, packed folders, blobs) goes in the worker
    (`project.purged` starts `purge_project_archive`)."""
    await projects.purge_project(s, body.id, body.reason, now=now)
    return PurgeOut(scope=body.scope, id=body.id)


# --- Seed writer (the acceptance seed, Scott decision 37) -------------------------------------


async def seed_link(workspace_id: UUID, task_id: UUID, rec: LinkSeed) -> UUID:
    """A seed task's link to a bare URL, as the system actor: outside content, so the link
    and its task are tainted (P2-08)."""
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415

    item = await link_context(
        WorkspaceContext(workspace_id, SYSTEM_ACTOR),
        owner_type="task",
        owner_id=task_id,
        target_type="url",
        target_url=rec.url,
        added_by=SYSTEM_ACTOR,
    )
    return item.id


register_seed_writer("link", seed_link)
