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

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import Table, or_, select, tuple_
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
from tumnis.core.tenancy import WorkspaceContext, session_for
from tumnis.core.versioning import NotFound
from tumnis.modules.integrations import rules
from tumnis.modules.integrations.models import (
    Artifact,
    Connection,
    ContextItem,
    Message,
    Note,
    Person,
    RawPayload,
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
    """Idempotent on (owner, target). tainted = target.tainted (rules.propagate_taint)."""
    raise NotImplementedError
