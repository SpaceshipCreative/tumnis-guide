"""Canonical integration records and the one upsert every connector write goes through
(P0-12, FR-14.1, FR-14.3).

Every integration lands in the same canonical records (Person, Thread, Message, Note,
Artifact, Event, Document), each a `CanonicalRecord` registered in the `entities` schema
family. A record's row is keyed on (workspace, connection, external id), so re-sending an
item never duplicates it; `content_hash` (the record's JSON without `fetched_at`) decides
whether a re-sent item changed. The owning module's upsert calls `upsert_records` with its
own table: integrations for people, threads, messages, notes and artifacts, calendar for
events, knowledge for documents. No module writes another module's table.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, Field
from sqlalchemy import ColumnElement, ForeignKey, LargeBinary, Table, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tumnis.core.schemas import VersionedPayload

# The unique key every canonical table carries, workspace first (P0-06 rule).
CANONICAL_KEY: tuple[str, ...] = ("workspace_id", "connection_id", "external_id")


class CanonicalRecord(VersionedPayload):
    """One provider object in canonical form. Subclasses pin `record_type` with a
    `Literal` ("message", "thread", "note", "person", "artifact", "event", "document") and
    `schema_version`, and register with `@versioned("entities", <record_type>, 1)`."""

    record_type: str
    external_id: str = Field(min_length=1)
    provider_url: str | None = None
    fetched_at: AwareDatetime


def content_hash(rec: CanonicalRecord) -> bytes:
    """sha256 of the record's JSON without `fetched_at`: a re-fetch of the same content
    hashes the same, so its upsert is a no-op."""
    raise NotImplementedError


@dataclass(frozen=True)
class UpsertStats:
    """What one upsert did: new rows, changed rows (content or a restored soft delete),
    and records whose row already held the same content. `changed_ids` are the ids of the
    inserted and updated rows."""

    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    changed_ids: tuple[UUID, ...] = ()

    def __add__(self, other: "UpsertStats") -> "UpsertStats":
        return UpsertStats(
            self.inserted + other.inserted,
            self.updated + other.updated,
            self.unchanged + other.unchanged,
            self.changed_ids + other.changed_ids,
        )


type ColumnMap[R: CanonicalRecord] = Callable[[R], Mapping[str, Any]]


async def upsert_records[R: CanonicalRecord](  # noqa: PLR0917  # the plan's signature
    session: AsyncSession,
    table: Table,
    connection_id: UUID,
    records: Sequence[R],
    raw_ids: Mapping[str, UUID],
    column_map: ColumnMap[R],
    *,
    source: str,
    conflict: Sequence[str] = CANONICAL_KEY,
    index_where: ColumnElement[bool] | None = None,
) -> UpsertStats:
    """INSERT ... ON CONFLICT (workspace_id, connection_id, external_id) DO UPDATE
    SET <cols>, deleted_at = NULL
    WHERE <table>.content_hash IS DISTINCT FROM EXCLUDED.content_hash
       OR <table>.deleted_at IS NOT NULL.

    Unchanged records are no-ops (no version bump, no updated_at change). Records repeated
    in `records` collapse to the last one. `raw_ids` maps a record's external id to its
    `raw_payloads` row; `column_map` gives the table-specific columns. `conflict` names
    another unique key (people upsert on their primary email) and `index_where` the
    predicate of a partial unique index (documents: external_id IS NOT NULL)."""
    raise NotImplementedError


async def soft_delete_records(
    session: AsyncSession, table: Table, connection_id: UUID, external_ids: Sequence[str]
) -> int:
    """Marks the connection's rows with these external ids deleted (the provider reported
    them gone); a later upsert of the same id restores the row. Returns the rows marked."""
    raise NotImplementedError


class CanonicalColumns:
    """The common canonical columns (`canonical_columns()` in the migrations) for a model
    that mirrors a canonical table."""

    connection_id: Mapped[UUID] = mapped_column(ForeignKey("connections.id"))
    external_id: Mapped[str]
    provider_url: Mapped[str | None]
    fetched_at: Mapped[datetime]
    raw_payload_id: Mapped[UUID | None] = mapped_column(ForeignKey("raw_payloads.id"))
    content_hash: Mapped[bytes] = mapped_column(LargeBinary)
    tainted: Mapped[bool] = mapped_column(server_default=text("true"))
    source: Mapped[str]
