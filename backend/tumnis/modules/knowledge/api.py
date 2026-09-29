"""knowledge public functions and DTOs; the only file other modules may import.

Knowledge owns the `documents` table (P0-12 creates it with the canonical columns, R-15;
P0-17 adds bodies and roles). A knowledge connector maps provider files to
`DocumentRecord`s and stores them with `upsert_synced_documents`; text entries and uploads
have no connection or external id, which is why those columns are nullable here and the
canonical unique key is partial.
"""

import hashlib
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import Table, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import tenancy
from tumnis.core.canonical import CanonicalRecord, UpsertStats, upsert_records
from tumnis.core.net import NetPolicy, Resolver, system_resolver
from tumnis.core.schemas import versioned
from tumnis.core.tenancy import WorkspaceContext, session_for, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.core.versioning import NotFound
from tumnis.modules.integrations import api as integrations
from tumnis.modules.knowledge.models import Document
from tumnis.modules.knowledge.storage import FileStat
from tumnis.seed import DocumentSeed, register_seed_writer

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


# --- Text entries and the project brief (P0-17, FR-2.3, FR-15.1, R-13) -------------------

BRIEF: Final = "brief"
TEXT_SOURCE: Final = "text"  # `source` of entries written in the app (no connection)


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


def _text_row(
    project_id: UUID | None, title: str, body_md: str, role: str | None
) -> dict[str, Any]:
    """A trusted text entry written in the app; the brief is pinned first (FR-15.1)."""
    return {
        "project_id": project_id,
        "title": title,
        "kind": "text",
        "role": role,
        "body_md": body_md,
        "trust": "trusted",
        "tainted": False,
        "pinned": role == BRIEF,
        "content_hash": hashlib.sha256(body_md.encode()).digest(),
        "source": TEXT_SOURCE,
    }


def _brief_conflict() -> dict[str, Any]:
    return {
        "index_elements": [_documents.c.workspace_id, _documents.c.project_id],
        "index_where": _documents.c.role == BRIEF,
    }


async def create_brief(s: AsyncSession, project_id: UUID, title: str, body_md: str) -> bool:
    """The project's brief, once: a second call (a redelivered `project.created`) finds
    the brief index taken and writes nothing. True when it wrote the row."""
    stmt = (
        pg_insert(_documents)
        .values(**_text_row(project_id, title, body_md, BRIEF))
        .on_conflict_do_nothing(**_brief_conflict())
        .returning(_documents.c.id)
    )
    return (await s.execute(stmt)).first() is not None


async def put_text_document(
    s: AsyncSession, project_id: UUID | None, *, title: str, body_md: str, role: str | None
) -> UUID:
    """A text entry; for `role="brief"` the project's brief is created or its title and
    body replaced (so a seed brief lands whether or not the subscriber ran first)."""
    stmt = pg_insert(_documents).values(**_text_row(project_id, title, body_md, role))
    if role == BRIEF:
        stmt = stmt.on_conflict_do_update(
            **_brief_conflict(),
            set_={
                "title": stmt.excluded.title,
                "body_md": stmt.excluded.body_md,
                "content_hash": stmt.excluded.content_hash,
            },
        )
    doc_id: UUID = (await s.execute(stmt.returning(_documents.c.id))).scalar_one()
    return doc_id


async def get_brief(project_id: UUID, *, session: AsyncSession | None = None) -> DocumentDTO:
    """The project's brief (R-13), in the caller's transaction when `session` is given,
    else in the current workspace context; NotFound until the `project.created`
    subscriber has run."""
    ctx = tenancy.current()
    if ctx is None:
        raise RuntimeError("get_brief runs in a workspace context")
    async with session_for(ctx, session) as s:
        row = (
            (
                await s.execute(
                    select(_documents).where(
                        _documents.c.project_id == project_id,
                        _documents.c.role == BRIEF,
                        _documents.c.deleted_at.is_(None),
                    )
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise NotFound("documents", project_id)
    return DocumentDTO.model_validate(dict(row))


async def seed_document(workspace_id: UUID, project_id: UUID | None, rec: DocumentSeed) -> UUID:
    """A seed text entry (the project briefs), written as the system actor."""
    async with tenant_session(WorkspaceContext(workspace_id, SYSTEM_ACTOR)) as s:
        return await put_text_document(
            s, project_id, title=rec.title, body_md=rec.body, role=rec.role
        )


register_seed_writer("document", seed_document)


# --- Storage locations and project folders (P1-14, FR-15.7, FR-15.12, SEC-5) -------------

LocationKind = Literal["server_path", "s3"]


class S3ConfigIn(BaseModel):
    endpoint: str  # http(s)://host[:port]; checked by the SSRF guard at save and each use
    region: str = "us-east-1"
    access_key: str
    secret_key: str  # write-only: sealed into config_enc, never answered
    path_style: bool = True
    sse: Literal["AES256"] | None = None


class LocationIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: LocationKind
    root: str = Field(min_length=1, max_length=1024)  # absolute path, or bucket/prefix
    s3: S3ConfigIn | None = None
    is_default: bool = False


class LocationOut(BaseModel):
    id: UUID
    name: str
    kind: str
    root: str
    endpoint: str | None  # S3 only; the keys are never answered
    status: Literal["online", "offline"]
    status_reason: str | None
    is_default: bool
    capabilities: dict[str, bool]
    version: int


class ProjectFolderOut(BaseModel):
    project_id: UUID
    location_id: UUID
    root_path: str
    mode: Literal["tumnis_made", "existing"]
    version: int


class NoteWrite(BaseModel):
    status: Literal["written", "queued"]
    location_id: UUID
    path: str  # relative to the location's root


async def create_location(
    s: AsyncSession, body: LocationIn, *, net: NetPolicy, resolver: Resolver = system_resolver
) -> LocationOut:
    raise NotImplementedError


async def list_locations(s: AsyncSession) -> list[LocationOut]:
    raise NotImplementedError


async def check_location(
    s: AsyncSession, location_id: UUID, *, net: NetPolicy, resolver: Resolver = system_resolver
) -> LocationOut:
    raise NotImplementedError


async def set_default_location(s: AsyncSession, location_id: UUID, version: int) -> LocationOut:
    raise NotImplementedError


async def assign_project_folder(s: AsyncSession, project_id: UUID) -> ProjectFolderOut | None:
    raise NotImplementedError


async def get_project_folder(s: AsyncSession, project_id: UUID) -> ProjectFolderOut:
    raise NotImplementedError


async def set_project_location(
    s: AsyncSession,
    project_id: UUID,
    location_id: UUID,
    *,
    net: NetPolicy,
    resolver: Resolver = system_resolver,
) -> ProjectFolderOut:
    raise NotImplementedError


async def write_project_file(
    s: AsyncSession,
    project_id: UUID,
    path: str,
    data: AsyncIterator[bytes],
    *,
    if_match: str | None = None,
    net: NetPolicy,
    resolver: Resolver = system_resolver,
) -> FileStat:
    raise NotImplementedError


async def save_note(
    s: AsyncSession,
    document_id: UUID,
    *,
    if_match: str | None = None,
    net: NetPolicy,
    resolver: Resolver = system_resolver,
) -> NoteWrite:
    raise NotImplementedError
