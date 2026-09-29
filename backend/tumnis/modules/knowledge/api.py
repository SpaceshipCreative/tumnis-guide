"""knowledge public functions and DTOs; the only file other modules may import.

Knowledge owns the `documents` table (P0-12 creates it with the canonical columns, R-15;
P0-17 adds bodies and roles). A knowledge connector maps provider files to
`DocumentRecord`s and stores them with `upsert_synced_documents`; text entries and uploads
have no connection or external id, which is why those columns are nullable here and the
canonical unique key is partial.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import Table, select
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.canonical import CanonicalRecord, UpsertStats, upsert_records
from tumnis.core.schemas import versioned
from tumnis.core.tenancy import WorkspaceContext, session_for
from tumnis.modules.integrations import api as integrations
from tumnis.modules.knowledge.models import Document

_documents: Table = Document.__table__  # type: ignore[assignment]


@versioned("entities", "document", 1)
class DocumentRecord(CanonicalRecord):
    schema_version: Literal[1] = 1
    record_type: Literal["document"] = "document"
    title: str
    kind: str = "file"
    path: str | None = None
    source_revision: str | None = None
    tags: list[str] = Field(default_factory=list)


async def upsert_synced_documents(
    ctx: WorkspaceContext,
    connection_id: UUID,
    records: Sequence[DocumentRecord],
    *,
    raw_ids: Mapping[str, UUID] | None = None,
    session: AsyncSession | None = None,
) -> UpsertStats:
    """Upserts the connection's synced documents on (workspace, connection, external id)
    (the partial unique key: text entries and uploads have no external id). New rows are
    untrusted and tainted (the column defaults); a re-sync never changes either."""
    async with session_for(ctx, session) as s:
        source = await integrations.connection_source(s, connection_id)
        return await upsert_records(
            s,
            _documents,
            connection_id,
            records,
            raw_ids or {},
            _columns,
            source=source,
            index_where=_documents.c.external_id.is_not(None),
        )


def _columns(rec: DocumentRecord) -> dict[str, Any]:
    return {
        "title": rec.title,
        "kind": rec.kind,
        "path": rec.path,
        "source_revision": rec.source_revision,
        "tags": rec.tags,
    }


async def _document_taint(session: AsyncSession, document_id: UUID) -> bool | None:
    tainted: bool | None = await session.scalar(
        select(_documents.c.tainted).where(_documents.c.id == document_id)
    )
    return tainted


integrations.register_target_taint("document", _document_taint)


# --- The project brief (P0-17, FR-2.3, R-13) ---------------------------------------------


class DocumentDTO(BaseModel):
    id: UUID
    project_id: UUID | None
    title: str
    kind: str
    role: str | None
    body_md: str | None
    trust: Literal["trusted", "untrusted"]
    tainted: bool
    pinned: bool
    version: int


async def get_brief(project_id: UUID, *, session: AsyncSession | None = None) -> DocumentDTO:
    raise NotImplementedError
