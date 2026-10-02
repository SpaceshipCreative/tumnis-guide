"""The S3 linked-source sync (P3-13, FR-15.11, FR-15.8, SEC-10): what
`knowledge_s3_source_sync` and `knowledge_s3_source_recheck` run.

`sync_source` lists every mapped prefix of the source's bucket (`ListObjectsV2`: current
versions only, so a versioned bucket shows its latest version, and a delete marker shows as
the key missing) and compares each object with the source's `folder_files` record through
`rules.s3_change`:

- new or changed: the object is downloaded to the extraction spool (refused past the
  upload limit before any byte is read), and becomes a Document of the prefix's project
  (`connection_id`, `external_id` = the key, `source` "s3", `source_revision` = its ETag)
  or that Document's next version. Its trust follows the source: untrusted and tainted
  unless the source is marked trusted. The version is `pending_scan` and goes through
  P1-16's pipeline (`source = "linked"`: scanned, sniffed and extracted from the spool,
  never placed in a project folder; the object stays in its bucket). Content equal to the
  last version (a multipart re-upload changes the ETag, not the bytes) only refreshes the
  record.
- deleted (recorded, no longer listed): the Document moves to the trash and the record
  goes.

`recheck_key` is what a MinIO notification queues: a HEAD of one key in the source's own
bucket. Only a key under a mapped prefix that exists and changed is taken in; anything
else (a key that does not exist, one outside every prefix) does nothing more, so a forged
notification can cause a stat and nothing else. A key a notification says is gone is left
to the next listing.
"""

import asyncio
import contextlib
import hashlib
import hmac
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final
from uuid import UUID

from sqlalchemy import RowMapping, Table, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.clock import Clock, SystemClock
from tumnis.core.ids import uuid7
from tumnis.core.live import mark_changed
from tumnis.core.net import NetPolicy
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.knowledge import api, pipeline, s3_sources, store
from tumnis.modules.knowledge.adapters.port import S3SourceReader
from tumnis.modules.knowledge.models import Document, DocumentVersion, FolderFile, S3Source
from tumnis.modules.knowledge.rules import (
    MAX_UPLOAD_BYTES,
    FolderFileLite,
    PathRejected,
    S3ObjectLite,
    prefix_for,
    s3_change,
)
from tumnis.modules.knowledge.storage import FileStat, NotFound

log = logging.getLogger(__name__)

SOURCE: Final = "s3"  # documents.source of an object of a linked bucket
LAST_OP: Final = "s3_sync"

ExtractHook = Callable[[UUID, UUID], Awaitable[None]]

_documents: Table = Document.__table__  # type: ignore[assignment]
_files: Table = FolderFile.__table__  # type: ignore[assignment]
_sources: Table = S3Source.__table__  # type: ignore[assignment]
_versions: Table = DocumentVersion.__table__  # type: ignore[assignment]


class _Config:
    net: NetPolicy | None = None
    clock: Clock | None = None


def configure(net: NetPolicy | None, clock: Clock | None = None) -> None:
    """The SSRF policy sources are read under (the worker's settings) and the clock."""
    _Config.net = net
    _Config.clock = clock


def net() -> NetPolicy:
    return _Config.net or api.deployment_net()


def _ctx(workspace_id: str | UUID) -> WorkspaceContext:
    return WorkspaceContext(UUID(str(workspace_id)), SYSTEM_ACTOR)


@dataclass(frozen=True)
class _Source:
    workspace_id: UUID
    connection_id: UUID
    trusted: bool
    projects: Mapping[str, UUID | None]  # prefix -> project


def _source(row: Mapping[Any, Any]) -> _Source:
    return _Source(
        workspace_id=row["workspace_id"],
        connection_id=row["connection_id"],
        trusted=row["trusted"],
        projects={
            p["prefix"]: (UUID(p["project_id"]) if p["project_id"] else None)
            for p in row["prefixes"]
        },
    )


@contextlib.asynccontextmanager
async def _opened(
    ctx: WorkspaceContext, connection_id: UUID, net_policy: NetPolicy
) -> AsyncGenerator[tuple[_Source, S3SourceReader]]:
    async with tenant_session(ctx) as s:
        row = await s3_sources.source_row(s, connection_id)
        reader = await s3_sources.open_source(s, row, net=net_policy)
    try:
        yield _source(row), reader
    finally:
        await reader.aclose()


async def _records(s: AsyncSession, connection_id: UUID) -> dict[str, RowMapping]:
    rows = (
        (await s.execute(select(_files).where(_files.c.connection_id == connection_id)))
        .mappings()
        .all()
    )
    return {row["path"]: row for row in rows}


def _lite(record: RowMapping | None) -> FolderFileLite | None:
    if record is None:
        return None
    return FolderFileLite(etag=record["etag"], size=record["size"], mtime=record["mtime"])


def _obj(stat: FileStat) -> S3ObjectLite:
    return S3ObjectLite(key=stat.path, etag=stat.etag, size=stat.size, last_modified=stat.mtime)


async def _listed(reader: S3SourceReader, prefixes: Sequence[str]) -> dict[str, FileStat]:
    found: dict[str, FileStat] = {}
    for prefix in prefixes:
        cursor: str | None = None
        while True:
            page = await reader.list(prefix, cursor)
            for stat in page.items:
                if prefix_for(stat.path, prefixes) is not None:
                    found[stat.path] = stat
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
    return found


async def _download(reader: S3SourceReader, key: str, dest: Path) -> str | None:
    """The object into `dest`, hashed; None (and nothing kept) past the upload limit, and
    nothing kept when the read fails (each attempt spools under a new name)."""
    await asyncio.to_thread(dest.parent.mkdir, parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    handle = await asyncio.to_thread(dest.open, "wb")
    try:
        try:
            async for chunk in reader.read(key):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    break
                digest.update(chunk)
                await asyncio.to_thread(handle.write, chunk)
        finally:
            await asyncio.to_thread(handle.close)
    except BaseException:
        await asyncio.to_thread(dest.unlink, missing_ok=True)
        raise
    if size > MAX_UPLOAD_BYTES:
        await asyncio.to_thread(dest.unlink, missing_ok=True)
        return None
    return digest.hexdigest()


async def _record(  # the record's columns
    s: AsyncSession,
    connection_id: UUID,
    stat: FileStat,
    *,
    content_hash: str,
    document_id: UUID,
    synced_version: int,
) -> None:
    values = {
        "size": stat.size,
        "mtime": stat.mtime,
        "etag": stat.etag,
        "content_hash": content_hash,
        "origin": "external",
        "document_id": document_id,
        "synced_version": synced_version,
        "last_op": LAST_OP,
    }
    stmt = pg_insert(_files).values(connection_id=connection_id, path=stat.path, **values)
    await s.execute(
        stmt.on_conflict_do_update(
            index_elements=[_files.c.workspace_id, _files.c.connection_id, _files.c.path],
            index_where=_files.c.connection_id.is_not(None),
            set_=values,
        )
    )


async def _document(  # the source, the object, and what it holds
    s: AsyncSession, source: _Source, stat: FileStat, project_id: UUID | None, digest: bytes
) -> UUID:
    """The key's Document (restored from the trash when the key came back), or a new one."""
    trust, tainted = ("trusted", False) if source.trusted else ("untrusted", True)
    values: dict[str, Any] = {
        "project_id": project_id,
        "content_hash": digest,
        "source_revision": stat.etag,
        "trust": trust,
        "tainted": tainted,
        "fetched_at": func.now(),
    }
    found = (
        (
            await s.execute(
                select(_documents.c.id, _documents.c.current_version_id).where(
                    _documents.c.connection_id == source.connection_id,
                    _documents.c.external_id == stat.path,
                )
            )
        )
        .mappings()
        .first()
    )
    if found is None:
        created: UUID = await s.scalar(
            _documents.insert()
            .values(
                title=PurePosixPath(stat.path).name,
                kind="file",
                source=SOURCE,
                status="pending_scan",
                connection_id=source.connection_id,
                external_id=stat.path,
                **values,
            )
            .returning(_documents.c.id)
        )
        return created
    if found["current_version_id"] is None:
        values |= {"status": "pending_scan", "status_reason": None}
    await s.execute(
        update(_documents).where(_documents.c.id == found["id"]).values(deleted_at=None, **values)
    )
    document_id: UUID = found["id"]
    return document_id


async def _take_in(  # the source, the object, where it goes, and the record so far
    ctx: WorkspaceContext,
    source: _Source,
    reader: S3SourceReader,
    stat: FileStat,
    record: RowMapping | None,
) -> UUID | None:
    """A new or changed object as the Document's next version; the version to extract,
    or None when nothing new came in."""
    prefix = prefix_for(stat.path, list(source.projects))
    if prefix is None:
        return None
    if stat.size > MAX_UPLOAD_BYTES:
        log.info("s3 source %s: %s is over the upload limit", source.connection_id, stat.path)
        return None
    version_id = uuid7()
    spooled = pipeline.spool_file(version_id)
    try:
        sha256 = await _download(reader, stat.path, spooled)
    except NotFound:
        return None  # gone since the listing: the next one trashes it
    if sha256 is None:
        return None
    try:
        async with tenant_session(ctx) as s:
            if record is not None and record["content_hash"] == sha256 and record["document_id"]:
                # The same bytes under a new ETag (a multipart re-upload): no new version.
                await _record(
                    s,
                    source.connection_id,
                    stat,
                    content_hash=sha256,
                    document_id=record["document_id"],
                    synced_version=record["synced_version"] or 1,
                )
                await asyncio.to_thread(spooled.unlink, missing_ok=True)
                return None
            project_id = source.projects[prefix]
            digest = bytes.fromhex(sha256)
            document_id = await _document(s, source, stat, project_id, digest)
            await store.add_version(
                s,
                document_id,
                version_id,
                digest=digest,
                size=stat.size,
                source_name=PurePosixPath(stat.path).name,
            )
            version = await s.scalar(
                select(_documents.c.version).where(_documents.c.id == document_id)
            )
            await _record(
                s,
                source.connection_id,
                stat,
                content_hash=sha256,
                document_id=document_id,
                synced_version=int(version or 1),
            )
            if project_id is not None:
                mark_changed(s, "project", project_id)
    except BaseException:  # nothing refers to the spooled copy: drop it
        await asyncio.to_thread(spooled.unlink, missing_ok=True)
        raise
    return version_id


async def _gone(ctx: WorkspaceContext, record: RowMapping) -> None:
    """The key is no longer listed: its Document to the trash, its record dropped."""
    async with tenant_session(ctx) as s:
        if record["document_id"] is not None:
            await s.execute(
                update(_documents)
                .where(_documents.c.id == record["document_id"], _documents.c.deleted_at.is_(None))
                .values(deleted_at=func.now())
            )
        await s.execute(delete(_files).where(_files.c.id == record["id"]))


async def _extract_all(
    ctx: WorkspaceContext, versions: Sequence[UUID], extract: ExtractHook | None
) -> None:
    for version_id in versions:
        if extract is not None:
            await extract(ctx.workspace_id, version_id)
        else:
            await api.enqueue_extract(ctx, version_id, "linked")


async def sync_source(
    ctx: WorkspaceContext,
    connection_id: UUID,
    *,
    net: NetPolicy,
    extract: ExtractHook | None = None,
) -> dict[str, int]:
    """One full comparison of the source's mapped prefixes with its records; the counts of
    versions taken in and documents trashed. `extract(workspace_id, version_id)` replaces
    the default extraction request (tests)."""
    taken: list[UUID] = []
    trashed = 0
    async with _opened(ctx, connection_id, net) as (source, reader):
        listed = await _listed(reader, list(source.projects))
        async with tenant_session(ctx) as s:
            records = await _records(s, connection_id)
        for key, stat in sorted(listed.items()):
            record = records.get(key)
            if s3_change(_lite(record), _obj(stat)) in ("new", "changed"):
                version_id = await _take_in(ctx, source, reader, stat, record)
                if version_id is not None:
                    # Requested at once (idempotent per version): a later failure retries
                    # the step, which then finds this key unchanged and would not ask again.
                    await _extract_all(ctx, [version_id], extract)
                    taken.append(version_id)
        for key, record in records.items():
            if key not in listed:
                await _gone(ctx, record)
                trashed += 1
    async with tenant_session(ctx) as s:
        await s.execute(
            update(_sources)
            .where(_sources.c.connection_id == connection_id)
            .values(last_sync_at=(_Config.clock or SystemClock()).now())
        )
    return {"taken": len(taken), "trashed": trashed}


async def recheck_key(
    ctx: WorkspaceContext,
    connection_id: UUID,
    key: str,
    *,
    net: NetPolicy,
    extract: ExtractHook | None = None,
) -> bool:
    """A HEAD of `key` in the source's own bucket; taken in only when it is under a mapped
    prefix, exists and changed. Whether a version was taken in."""
    async with _opened(ctx, connection_id, net) as (source, reader):
        if prefix_for(key, list(source.projects)) is None:
            return False
        try:
            stat = await reader.stat(key)
        except (ValueError, PathRejected, NotFound):
            return False  # a key Tumnis cannot address, or gone
        if stat is None:
            return False
        async with tenant_session(ctx) as s:
            record = (await _records(s, connection_id)).get(key)
        if s3_change(_lite(record), _obj(stat)) not in ("new", "changed"):
            return False
        version_id = await _take_in(ctx, source, reader, stat, record)
        if version_id is None:
            return False
        await _extract_all(ctx, [version_id], extract)
    return True


async def read_linked(ctx: WorkspaceContext, version_id: UUID) -> AsyncGenerator[bytes]:
    """A linked version's object, read again from its bucket (the pipeline's fallback when
    the spool copy is gone). The object may have changed since: another hash at the end,
    or more bytes than any version can hold (step 1 would stop reading before the end),
    raises `pipeline.LinkedObjectChangedError`, so step 1 never stores the new bytes' hash on
    the old version."""
    async with tenant_session(ctx) as s:
        found = (
            await s.execute(
                select(
                    _documents.c.connection_id,
                    _documents.c.external_id,
                    _versions.c.content_hash,
                )
                .join(_versions, _versions.c.document_id == _documents.c.id)
                .where(_versions.c.id == version_id)
            )
        ).first()
    if found is None or found.connection_id is None or found.external_id is None:
        raise FileNotFoundError(f"version {version_id} is not from a linked source")
    changed = pipeline.LinkedObjectChangedError(
        f"{found.external_id} changed since version {version_id}"
    )
    digest = hashlib.sha256()
    size = 0
    async with _opened(ctx, found.connection_id, net()) as (_, reader):
        async for chunk in reader.read(found.external_id):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise changed
            digest.update(chunk)
            yield chunk
    if not hmac.compare_digest(digest.digest(), bytes(found.content_hash)):
        raise changed


async def sources() -> list[tuple[str, str]]:
    """(workspace, connection) for every live S3 source of every workspace."""
    from tumnis.core import audit, db  # noqa: PLC0415

    async with db.app_sessionmaker()() as s, s.begin():
        workspaces = await audit.workspace_ids(s)
    found: list[tuple[str, str]] = []
    for workspace_id in workspaces:
        async with tenant_session(_ctx(workspace_id)) as s:
            ids: Iterable[UUID] = await s.scalars(
                select(_sources.c.connection_id).where(_sources.c.deleted_at.is_(None))
            )
            found += [(str(workspace_id), str(i)) for i in ids]
    return found


pipeline.register_linked_reader(read_linked)
