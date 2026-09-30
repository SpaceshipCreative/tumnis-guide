"""The SQL behind the extraction pipeline (P1-16): the rows an upload or a folder file
becomes, their statuses, what each step hands the next (`extraction_artifacts`) and the
chunks. Every function takes the caller's session (inside `tenant_session`); nothing here
commits. `api.py` and `pipeline.py` are the only callers.

A document's status follows its newest version's until the document has a current version;
after that a later version that fails or is quarantined leaves the document `ready` on the
version it already serves.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal
from uuid import UUID

from sqlalchemy import Table, delete, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.clock import SystemClock
from tumnis.core.outbox import emit
from tumnis.core.versioning import NotFound
from tumnis.modules.knowledge.adapters.port import ChunkRow
from tumnis.modules.knowledge.models import Chunk, Document, DocumentVersion, ExtractionArtifact
from tumnis.modules.knowledge.payloads import DocumentAddedV1, DocumentChangedV1

Status = Literal["pending_scan", "extracting", "ready", "quarantined", "failed"]
NO_HASH: Final = bytes(32)  # a folder file's hash until step 1 has read it

_documents: Table = Document.__table__  # type: ignore[assignment]
_versions: Table = DocumentVersion.__table__  # type: ignore[assignment]
_artifacts: Table = ExtractionArtifact.__table__  # type: ignore[assignment]
_chunks: Table = Chunk.__table__  # type: ignore[assignment]


@dataclass(frozen=True)
class VersionInfo:
    """A version with the document it belongs to."""

    version_id: UUID
    document_id: UUID
    version_no: int
    size: int
    status: str
    source_name: str | None
    mime: str | None
    project_id: UUID | None
    title: str
    kind: str
    trust: str
    path: str | None
    location_id: UUID | None
    current_version_id: UUID | None


async def version_info(s: AsyncSession, version_id: UUID) -> VersionInfo:
    row = (
        (
            await s.execute(
                select(
                    _versions.c.id.label("version_id"),
                    _versions.c.document_id,
                    _versions.c.version_no,
                    _versions.c.size,
                    _versions.c.status,
                    _versions.c.source_name,
                    _versions.c.mime,
                    _documents.c.project_id,
                    _documents.c.title,
                    _documents.c.kind,
                    _documents.c.trust,
                    _documents.c.path,
                    _documents.c.storage_location_id.label("location_id"),
                    _documents.c.current_version_id,
                )
                .join(_documents, _documents.c.id == _versions.c.document_id)
                .where(_versions.c.id == version_id)
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound("document_versions", version_id)
    return VersionInfo(**row)


async def find_by_path(s: AsyncSession, project_id: UUID, path: str) -> UUID | None:
    """The live document of a folder file already known at `path` in the project."""
    found: UUID | None = await s.scalar(
        select(_documents.c.id).where(
            _documents.c.project_id == project_id,
            _documents.c.path == path,
            _documents.c.source == "folder",
            _documents.c.deleted_at.is_(None),
        )
    )
    return found


async def new_document(
    s: AsyncSession,
    *,
    project_id: UUID | None,
    title: str,
    digest: bytes,
    location_id: UUID | None,
    path: str | None,
    source: Literal["upload", "folder"],
) -> UUID:
    """An untrusted, tainted document, `pending_scan`; its kind is settled by the sniff."""
    created: UUID = (
        await s.execute(
            insert(_documents)
            .values(
                project_id=project_id,
                title=title,
                kind="file",
                trust="untrusted",
                tainted=True,
                storage_location_id=location_id,
                path=path,
                content_hash=digest,
                source=source,
                status="pending_scan",
            )
            .returning(_documents.c.id)
        )
    ).scalar_one()
    return created


async def add_version(
    s: AsyncSession,
    document_id: UUID,
    version_id: UUID,
    *,
    digest: bytes,
    size: int,
    source_name: str,
) -> int:
    """The document's next version, `pending_scan`; returns its number."""
    number: int = (
        await s.scalar(
            select(func.coalesce(func.max(_versions.c.version_no), 0) + 1).where(
                _versions.c.document_id == document_id
            )
        )
    ) or 1
    await s.execute(
        insert(_versions).values(
            id=version_id,
            document_id=document_id,
            version_no=number,
            content_hash=digest,
            size=size,
            status="pending_scan",
            source_name=source_name,
        )
    )
    return number


async def set_status(
    s: AsyncSession, version_id: UUID, status: Status, reason: str | None = None
) -> None:
    """The version's status, and the document's while the document has no other current
    version to serve."""
    document_id = await s.scalar(
        update(_versions)
        .where(_versions.c.id == version_id)
        .values(status=status, status_reason=reason)
        .returning(_versions.c.document_id)
    )
    if document_id is None:
        raise NotFound("document_versions", version_id)
    await s.execute(
        update(_documents)
        .where(
            _documents.c.id == document_id,
            or_(
                _documents.c.current_version_id.is_(None),
                _documents.c.current_version_id == version_id,
            ),
        )
        .values(status=status, status_reason=reason)
    )


async def set_content(s: AsyncSession, version_id: UUID, *, digest: bytes, size: int) -> None:
    """What step 1 measured: the version's hash and size (and the document's hash)."""
    document_id = await s.scalar(
        update(_versions)
        .where(_versions.c.id == version_id)
        .values(content_hash=digest, size=size)
        .returning(_versions.c.document_id)
    )
    await s.execute(
        update(_documents).where(_documents.c.id == document_id).values(content_hash=digest)
    )


async def set_sniffed(s: AsyncSession, version_id: UUID, *, mime: str, kind: str | None) -> None:
    """What the sniff found: the version's MIME type and, when the type is allowed, the
    document's kind."""
    document_id = await s.scalar(
        update(_versions)
        .where(_versions.c.id == version_id)
        .values(mime=mime)
        .returning(_versions.c.document_id)
    )
    if kind is not None:
        await s.execute(update(_documents).where(_documents.c.id == document_id).values(kind=kind))


async def set_path(s: AsyncSession, document_id: UUID, path: str) -> None:
    await s.execute(update(_documents).where(_documents.c.id == document_id).values(path=path))


async def put_artifact(s: AsyncSession, version_id: UUID, stage: str, data: bytes) -> None:
    """One step's output for the version, replacing what a rerun left."""
    stmt = pg_insert(_artifacts).values(version_id=version_id, stage=stage, data=data)
    await s.execute(
        stmt.on_conflict_do_update(
            index_elements=[_artifacts.c.workspace_id, _artifacts.c.version_id, _artifacts.c.stage],
            set_={"data": stmt.excluded.data},
        )
    )


async def get_artifact(s: AsyncSession, version_id: UUID, stage: str) -> bytes | None:
    data = await s.scalar(
        select(_artifacts.c.data).where(
            _artifacts.c.version_id == version_id, _artifacts.c.stage == stage
        )
    )
    return None if data is None else bytes(data)


async def set_document(
    s: AsyncSession, version_id: UUID, *, docling_json: bytes, body_md: str
) -> None:
    """The compressed Docling document and the Markdown export, on the version."""
    await s.execute(
        update(_versions)
        .where(_versions.c.id == version_id)
        .values(docling_json=docling_json, body_md=body_md)
    )


async def replace_chunks(
    s: AsyncSession, document_id: UUID, version_id: UUID, rows: Sequence[ChunkRow]
) -> None:
    """The version's chunks are exactly `rows`: a rerun replaces, never doubles."""
    await s.execute(delete(_chunks).where(_chunks.c.document_version_id == version_id))
    if rows:
        await s.execute(
            insert(_chunks),
            [
                {
                    "document_id": document_id,
                    "document_version_id": version_id,
                    "ordinal": row.ordinal,
                    "text": row.text,
                    "context_text": row.context_text,
                    "heading_path": row.heading_path,
                    "page_from": row.page_from,
                    "page_to": row.page_to,
                    "extractor": row.extractor,
                }
                for row in rows
            ],
        )


async def mark_ready(s: AsyncSession, version_id: UUID) -> str:
    """Version and document `ready`, the version current, and the event that says so, in
    the caller's transaction: `document.added` for a document's first current version,
    `document.changed` after that. Returns the event's name, or "already_ready" (no event)
    when an earlier attempt of the step committed."""
    info = await version_info(s, version_id)
    if info.status == "ready":
        return "already_ready"
    await s.execute(
        update(_versions)
        .where(_versions.c.id == version_id)
        .values(status="ready", status_reason=None)
    )
    await s.execute(
        update(_documents)
        .where(_documents.c.id == info.document_id)
        .values(status="ready", status_reason=None, current_version_id=version_id)
    )
    fields = {
        "document_id": info.document_id,
        "version_id": version_id,
        "version_no": info.version_no,
        "project_id": info.project_id,
        "title": info.title,
        "trust": info.trust,
        "size": info.size,
    }
    now = SystemClock().now()
    if info.current_version_id is None:
        await emit(s, DocumentAddedV1.model_validate(fields), occurred_at=now)
        return DocumentAddedV1.event_name
    await emit(s, DocumentChangedV1.model_validate(fields), occurred_at=now)
    return DocumentChangedV1.event_name
