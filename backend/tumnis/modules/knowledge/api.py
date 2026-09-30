"""knowledge public functions and DTOs; the only file other modules may import.

Knowledge owns the `documents` table (P0-12 creates it with the canonical columns, R-15;
P0-17 adds bodies and roles). A knowledge connector maps provider files to
`DocumentRecord`s and stores them with `upsert_synced_documents`; text entries and uploads
have no connection or external id, which is why those columns are nullable here and the
canonical unique key is partial.
"""

import dataclasses
import hashlib
import json
import os
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import RowMapping, Table, delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import deadletter, settings_store, tenancy
from tumnis.core.adapters.errors import AdapterError, AdapterRejected, AdapterUnavailable
from tumnis.core.adapters.registry import current_mode
from tumnis.core.canonical import CanonicalRecord, UpsertStats, upsert_records
from tumnis.core.errors import ProblemError
from tumnis.core.ids import uuid7
from tumnis.core.live import mark_changed
from tumnis.core.net import NetPolicy, Resolver, SsrfBlocked, resolve_and_check, system_resolver
from tumnis.core.routing import register_project_lookup
from tumnis.core.schemas import versioned
from tumnis.core.tenancy import WorkspaceContext, session_for, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.core.versioning import NotFound, StaleVersion, update_versioned
from tumnis.modules.integrations import api as integrations
from tumnis.modules.knowledge import store
from tumnis.modules.knowledge.adapters.fake import FakeStorage
from tumnis.modules.knowledge.adapters.s3 import (
    S3Config,
    S3Storage,
    endpoint_host_port,
    endpoint_needs_https,
    endpoint_policy,
)
from tumnis.modules.knowledge.adapters.server_path import ServerPathStorage
from tumnis.modules.knowledge.models import (
    Document,
    DocumentVersion,
    PendingWrite,
    ProjectFolder,
    StorageLocation,
)
from tumnis.modules.knowledge.rules import is_network_fs, safe_rel_path
from tumnis.modules.knowledge.storage import (
    FileStat,
    Health,
    LocationOffline,
    PathRejected,
    PreconditionFailed,
    StorageBackend,
    StorageError,
    TooLarge,
    safe_prefix,
    spool,
)
from tumnis.modules.knowledge.storage import NotFound as FileMissing
from tumnis.modules.projects import api as projects
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
    # The predicate is literal SQL (issue #56): bound as `role = $1`, it cannot prove the
    # partial index's `role = 'brief'` once Postgres plans the prepared statement
    # generically, and the upsert fails with "no unique or exclusion constraint".
    return {
        "index_elements": [_documents.c.workspace_id, _documents.c.project_id],
        "index_where": text("role = 'brief'"),
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
#
# A location is a server path or an S3 bucket/prefix; its S3 endpoint and keys are sealed
# in `config_enc` with the workspace data key. Each project gets a folder
# (`<project id>/`) on the workspace default location when it is created. A location whose
# health is degraded (a share without its marker, an unreachable bucket) goes offline:
# uploads to it answer 409 `location_offline`, note saves (whose text is already in
# Postgres) queue in `pending_writes`, and the next healthy check drains the queue in
# insertion order. Every storage call goes through `open_backend`.

LocationKind = Literal["server_path", "s3"]
Row = Mapping[Any, Any]  # a location or folder row (RowMapping), or the dict of one

_locations: Table = StorageLocation.__table__  # type: ignore[assignment]
_folders: Table = ProjectFolder.__table__  # type: ignore[assignment]
_versions: Table = DocumentVersion.__table__  # type: ignore[assignment]
_pending: Table = PendingWrite.__table__  # type: ignore[assignment]

_BUCKET: Final = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_MOUNTINFO: Final = Path("/proc/self/mountinfo")
# Per-location fakes in fakes mode (TUMNIS_ADAPTERS=fake): the tree outlives one request.
_FAKES: dict[UUID, FakeStorage] = {}


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


_PROBLEMS: Final[tuple[tuple[type[Exception], int, str, str], ...]] = (
    (LocationOffline, 409, "location_offline", "The location is offline."),
    (PathRejected, 422, "path_rejected", "That path is not allowed."),
    (TooLarge, 413, "too_large", "The file is over 50 MiB."),
    (FileMissing, 404, "not_found", "No file at that path."),
    (SsrfBlocked, 422, "ssrf_blocked", "That endpoint is not allowed."),
    (AdapterRejected, 502, "location_refused", "The location refused the request."),
)


def _storage_problem(exc: Exception) -> ProblemError:
    """A storage or adapter failure as the problem the caller sees."""
    if isinstance(exc, PreconditionFailed):
        current = exc.current.model_dump(mode="json") if exc.current else None
        return ProblemError(409, "precondition_failed", "The file changed.", current=current)
    for kind, status, code, detail in _PROBLEMS:
        if isinstance(exc, kind):
            return ProblemError(status, code, detail)
    return ProblemError(503, "location_unavailable", "The location did not answer.")


def _s3_root(root: str) -> tuple[str, str]:
    """`bucket/prefix` as (bucket, prefix); ProblemError 422 when either is unusable."""
    bucket, _, prefix = root.strip("/").partition("/")
    if not _BUCKET.match(bucket):
        raise ProblemError(422, "invalid_location", "The bucket name is not valid.")
    try:
        safe_prefix(prefix + "/" if prefix else "")
    except PathRejected as exc:
        raise ProblemError(422, "invalid_location", "The prefix is not a safe path.") from exc
    return bucket, prefix


def _refuse_hosted_server_path(kind: str, net: NetPolicy) -> None:
    """Hosted mode offers S3 and SFTP only (FR-15.7): a server path there would let one
    workspace reach another's folders on the shared server."""
    if kind == "server_path" and net.mode == "hosted":
        raise ProblemError(
            422, "invalid_location", "Hosted Tumnis stores files in S3 or SFTP, not server folders."
        )


def _server_root(root: str) -> str:
    path = PurePosixPath(root)
    if not path.is_absolute() or ".." in path.parts:
        raise ProblemError(422, "invalid_location", "The folder must be an absolute path.")
    return str(path)


def _fstype(root: str) -> str | None:
    """The filesystem type of the mount holding `root`, from /proc/self/mountinfo (Linux);
    None elsewhere."""
    try:
        lines = _MOUNTINFO.read_text().splitlines()
    except OSError:
        return None
    best, fstype = "", None
    target = os.path.realpath(root)
    for line in lines:
        fields, _, rest = line.partition(" - ")
        parts = fields.split()
        mount = parts[4] if len(parts) > 4 else ""  # noqa: PLR2004
        inside = target == mount or target.startswith(mount.rstrip("/") + "/")
        if mount and inside and len(mount) >= len(best):
            best, fstype = mount, next(iter(rest.split()), None)
    return fstype


def _s3_config(blob: bytes) -> S3Config:
    data = json.loads(blob)
    return S3Config(
        endpoint=data["endpoint"],
        region=data["region"],
        access_key=data["access_key"],
        secret_key=data["secret_key"],
        path_style=data["path_style"],
        sse=data.get("sse"),
    )


async def _open_config(s: AsyncSession, row: Row) -> S3Config:
    blob = await settings_store.open_for_workspace(
        s, row["workspace_id"], row["config_enc"], aad=_aad(row["id"])
    )
    return _s3_config(blob)


def _aad(location_id: UUID) -> bytes:
    return f"storage_locations:{location_id}".encode()


def _backend(
    row: Row,
    config: S3Config | None,
    *,
    net: NetPolicy,
    resolver: Resolver,
) -> StorageBackend:
    _refuse_hosted_server_path(row["kind"], net)
    if current_mode() == "fake":
        return _FAKES.setdefault(row["id"], FakeStorage())
    caps = row["capabilities"] or {}
    if row["kind"] == "server_path":
        return ServerPathStorage(row["root"], network_fs=bool(caps.get("network_fs")))
    if row["kind"] == "s3" and config is not None:
        bucket, prefix = _s3_root(row["root"])
        return S3Storage(
            config,
            bucket=bucket,
            prefix=prefix,
            conditional_put=bool(caps.get("conditional_put")),
            health_write=bool(row["is_default"]),
            net_policy=net,
            resolver=resolver,
        )
    raise ProblemError(422, "invalid_location", f"{row['kind']} locations are not supported yet")


@asynccontextmanager
async def _opened(
    s: AsyncSession, row: RowMapping, *, net: NetPolicy, resolver: Resolver
) -> AsyncIterator[StorageBackend]:
    config = await _open_config(s, row) if row["config_enc"] is not None else None
    backend = _backend(row, config, net=net, resolver=resolver)
    try:
        yield backend
    finally:
        if isinstance(backend, S3Storage):
            await backend.aclose()


async def _location_row(s: AsyncSession, location_id: UUID) -> RowMapping:
    row = (
        (
            await s.execute(
                select(_locations).where(
                    _locations.c.id == location_id, _locations.c.deleted_at.is_(None)
                )
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound("storage_locations", location_id)
    return row


def open_backend(
    s: AsyncSession,
    location_id: UUID,
    *,
    net: NetPolicy,
    resolver: Resolver = system_resolver,
) -> AbstractAsyncContextManager[StorageBackend]:
    """The one way to reach a location's files: `async with open_backend(...) as b`. In
    fakes mode each location is a `FakeStorage` kept for the process."""

    @asynccontextmanager
    async def opened() -> AsyncIterator[StorageBackend]:
        row = await _location_row(s, location_id)
        async with _opened(s, row, net=net, resolver=resolver) as backend:
            yield backend

    return opened()


async def _health(backend: StorageBackend) -> Health:
    try:
        return await backend.health()
    except (StorageError, AdapterError) as exc:
        return Health.degraded(getattr(exc, "code", None) or type(exc).__name__)


async def _location_out(s: AsyncSession, row: Row) -> LocationOut:
    endpoint = None
    if row["kind"] == "s3" and row["config_enc"] is not None:
        endpoint = (await _open_config(s, row)).endpoint
    return LocationOut(
        id=row["id"],
        name=row["name"],
        kind=row["kind"],
        root=row["root"],
        endpoint=endpoint,
        status=row["status"],
        status_reason=row["status_reason"],
        is_default=row["is_default"],
        capabilities={k: bool(v) for k, v in (row["capabilities"] or {}).items()},
        version=row["version"],
    )


async def _set_status(s: AsyncSession, location_id: UUID, health: Health) -> RowMapping:
    online = health.status == "ok"
    stmt = (
        update(_locations)
        .where(_locations.c.id == location_id)
        .values(
            status="online" if online else "offline",
            status_reason=None if online else health.reason,
        )
        .returning(*_locations.c)
    )
    return (await s.execute(stmt)).mappings().one()


async def _clear_default(s: AsyncSession, keep: UUID | None) -> None:
    stmt = update(_locations).where(_locations.c.is_default, _locations.c.deleted_at.is_(None))
    if keep is not None:
        stmt = stmt.where(_locations.c.id != keep)
    await s.execute(stmt.values(is_default=False))


async def create_location(
    s: AsyncSession, body: LocationIn, *, net: NetPolicy, resolver: Resolver = system_resolver
) -> LocationOut:
    """Save a location after checking it: the root's shape, the S3 endpoint against the
    SSRF guard (422 `ssrf_blocked`, nothing saved), then its health and capabilities (a
    share's filesystem type; whether the provider honours conditional puts). The first
    location, or one saved with `is_default`, becomes the workspace default."""
    location_id = uuid7()
    taken = await s.scalar(
        select(_locations.c.id).where(
            func.lower(_locations.c.name) == body.name.lower(), _locations.c.deleted_at.is_(None)
        )
    )
    if taken is not None:
        raise ProblemError(409, "name_taken", "A location with that name exists.")
    config: S3Config | None = None
    _refuse_hosted_server_path(body.kind, net)
    if body.kind == "server_path":
        root = _server_root(body.root)
        fstype = _fstype(root)
        caps = {"network_fs": fstype is not None and is_network_fs(fstype)}
    else:
        if body.s3 is None:
            raise ProblemError(422, "invalid_location", "An S3 location needs its endpoint.")
        bucket, prefix = _s3_root(body.root)
        root = f"{bucket}/{prefix}" if prefix else bucket
        config = S3Config(**body.s3.model_dump())
        await _check_endpoint(config, net, resolver)
        caps = {"conditional_put": False}
    row: dict[str, Any] = {
        "id": location_id,
        "name": body.name,
        "kind": body.kind,
        "root": root,
        "config_enc": None,
        "is_default": False,
        "capabilities": caps,
    }
    health, caps = await _first_check(row, config, caps, net=net, resolver=resolver)
    if config is not None:
        workspace_id = await s.scalar(text("SELECT app.current_workspace_id()"))
        blob = json.dumps(dataclasses.asdict(config)).encode()
        _, row["config_enc"] = await settings_store.seal_for_workspace(
            s, workspace_id, blob, aad=_aad(location_id)
        )
    has_default = await s.scalar(
        select(_locations.c.id).where(_locations.c.is_default, _locations.c.deleted_at.is_(None))
    )
    if body.is_default:
        await _clear_default(s, None)
    online = health.status == "ok"
    row |= {
        "capabilities": caps,
        "is_default": body.is_default or has_default is None,
        "status": "online" if online else "offline",
        "status_reason": None if online else health.reason,
    }
    saved = (await s.execute(insert(_locations).values(**row).returning(*_locations.c))).mappings()
    return await _location_out(s, saved.one())


async def _check_endpoint(config: S3Config, net: NetPolicy, resolver: Resolver) -> None:
    url = config.endpoint_url()
    try:
        host, port = endpoint_host_port(url)
        await resolve_and_check(host, port, endpoint_policy(net, url), resolver)
        if endpoint_needs_https(net, url):  # after the guard, so a private host says so
            raise ProblemError(422, "invalid_location", "Hosted Tumnis needs an https endpoint.")
    except ValueError as exc:
        raise ProblemError(422, "invalid_location", "The endpoint is not an http(s) URL.") from exc
    except SsrfBlocked as exc:
        raise _storage_problem(exc) from exc
    except AdapterError as exc:
        raise ProblemError(422, "invalid_location", "The endpoint does not resolve.") from exc


async def _first_check(
    row: Row,
    config: S3Config | None,
    caps: dict[str, bool],
    *,
    net: NetPolicy,
    resolver: Resolver,
) -> tuple[Health, dict[str, bool]]:
    """Health, and for S3 the conditional-write probe, before the row exists."""
    backend = _backend(row, config, net=net, resolver=resolver)
    try:
        health = await _health(backend)
        if isinstance(backend, S3Storage) and health.status == "ok":
            try:
                probe = await backend.probe_conditional_writes()
            except (StorageError, AdapterError):
                pass  # unknown: the HEAD check covers writes until the next test
            else:
                caps = {**caps, "conditional_put": probe.conditional_put}
    finally:
        if isinstance(backend, S3Storage):
            await backend.aclose()
    return health, caps


async def list_locations(s: AsyncSession) -> list[LocationOut]:
    rows = (
        (
            await s.execute(
                select(_locations)
                .where(_locations.c.deleted_at.is_(None))
                .order_by(func.lower(_locations.c.name), _locations.c.id)
            )
        )
        .mappings()
        .all()
    )
    return [await _location_out(s, row) for row in rows]


async def check_location(
    s: AsyncSession, location_id: UUID, *, net: NetPolicy, resolver: Resolver = system_resolver
) -> LocationOut:
    """Test the connection now: the location's status follows its health, and a healthy
    location drains its queued writes. An S3 endpoint passes the SSRF guard again first
    (its name may resolve elsewhere now, or the deployment may run hosted): a blocked one
    is refused with 422 `ssrf_blocked`, and the location is not opened."""
    row = await _location_row(s, location_id)
    if row["kind"] == "s3" and row["config_enc"] is not None:
        await _check_endpoint(await _open_config(s, row), net, resolver)
    async with _opened(s, row, net=net, resolver=resolver) as backend:
        health = await _health(backend)
        row = await _set_status(s, location_id, health)
        if health.status == "ok":
            await _drain(s, location_id, backend)
    return await _location_out(s, row)


async def _drain(s: AsyncSession, location_id: UUID, backend: StorageBackend) -> None:
    """Write the location's queued notes in insertion order, deleting each row once its
    bytes are there. A precondition failure whose current content is the queued content
    counts as landed (a crash between the write and the delete); any other is a conflict
    left queued for the sync engine (P1-15). Rows for one path chain: each after the first
    takes the etag the previous one left, since they were queued against the same file.
    An outage stops the drain."""
    queued = (
        (
            await s.execute(
                select(_pending, _versions.c.body_md, _versions.c.content_hash)
                .join(_versions, _versions.c.id == _pending.c.document_version_id)
                .where(_pending.c.location_id == location_id, _pending.c.deleted_at.is_(None))
                .order_by(_pending.c.created_at, _pending.c.id)
            )
        )
        .mappings()
        .all()
    )
    landed: dict[str, str] = {}  # path -> etag an earlier row of this drain left there
    for item in queued:
        body, path = item["body_md"].encode(), item["path"]
        try:
            written = await backend.write(
                path, _one_chunk(body), landed.get(path, item["if_match"])
            )
        except PreconditionFailed as exc:
            if exc.current is None or not await _holds(backend, path, body, exc.current):
                await _note_attempt(s, item["id"], "precondition_failed")
                continue
            written = exc.current
        except (StorageError, AdapterError) as exc:
            await _note_attempt(s, item["id"], type(exc).__name__)
            return
        landed[path] = written.etag
        await s.execute(delete(_pending).where(_pending.c.id == item["id"]))


async def _holds(backend: StorageBackend, path: str, body: bytes, current: FileStat | None) -> bool:
    """The file at `path` holds exactly `body`. Decided on the bytes: etags differ per
    backend (sha256 on a server path, the provider's ETag, an MD5 for a single put, on
    S3). Notes are small, and a read failure is no match."""
    if current is None or current.size != len(body):
        return False
    try:
        return await spool(backend.read(path), limit=len(body)) == body
    except (StorageError, AdapterError):
        return False


async def _note_attempt(s: AsyncSession, pending_id: UUID, error: str) -> None:
    await s.execute(
        update(_pending)
        .where(_pending.c.id == pending_id)
        .values(attempts=_pending.c.attempts + 1, last_error=error)
    )


async def _one_chunk(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def set_default_location(s: AsyncSession, location_id: UUID, version: int) -> LocationOut:
    """Make the location the workspace default (versioned: StaleVersion on a stale row)."""
    await _location_row(s, location_id)
    await _clear_default(s, location_id)
    row = await update_versioned(s, _locations, location_id, version, {"is_default": True})
    return await _location_out(s, row)


def _folder_out(row: Row) -> ProjectFolderOut:
    return ProjectFolderOut.model_validate(dict(row))


async def assign_project_folder(s: AsyncSession, project_id: UUID) -> ProjectFolderOut | None:
    """The project's folder (`<project id>`) on the workspace default location, once: a
    second call finds it and writes nothing. None while the workspace has no default."""
    default = await s.scalar(
        select(_locations.c.id).where(_locations.c.is_default, _locations.c.deleted_at.is_(None))
    )
    if default is None:
        return None
    await s.execute(
        pg_insert(_folders)
        .values(project_id=project_id, location_id=default, root_path=str(project_id))
        .on_conflict_do_nothing(index_elements=[_folders.c.workspace_id, _folders.c.project_id])
    )
    return await get_project_folder(s, project_id)


async def _folder_row(s: AsyncSession, project_id: UUID) -> RowMapping:
    row = (
        (
            await s.execute(
                select(_folders).where(
                    _folders.c.project_id == project_id, _folders.c.deleted_at.is_(None)
                )
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound("project_folders", project_id)
    return row


async def get_project_folder(s: AsyncSession, project_id: UUID) -> ProjectFolderOut:
    return _folder_out(await _folder_row(s, project_id))


def _require_online(row: Row) -> None:
    if row["status"] != "online":
        raise ProblemError(409, "location_offline", "The location is offline.")


async def set_project_location(
    s: AsyncSession,
    project_id: UUID,
    location_id: UUID,
    *,
    net: NetPolicy,
    resolver: Resolver = system_resolver,
) -> ProjectFolderOut:
    """Move the project's folder to another location while it holds no file (409
    `folder_not_empty` once it does; moving files is P3-14). The old location must answer
    (409 `location_offline`), so an unreachable folder is never assumed empty. A project
    made before the workspace had a location has no folder yet: it is made on the target."""
    target = await _location_row(s, location_id)
    try:
        folder = await _folder_row(s, project_id)
    except NotFound:
        if not await projects.project_exists(s, project_id):
            raise
        await s.execute(
            pg_insert(_folders)
            .values(project_id=project_id, location_id=target["id"], root_path=str(project_id))
            .on_conflict_do_nothing(index_elements=[_folders.c.workspace_id, _folders.c.project_id])
        )
        return await get_project_folder(s, project_id)
    if folder["location_id"] == target["id"]:
        return _folder_out(folder)
    old = await _location_row(s, folder["location_id"])
    _require_online(old)
    async with _opened(s, old, net=net, resolver=resolver) as backend:
        if (await _health(backend)).status != "ok":
            raise ProblemError(409, "location_offline", "The current location is offline.")
        try:
            page = await backend.list(folder["root_path"] + "/", None)
        except (StorageError, AdapterError) as exc:
            raise _storage_problem(exc) from exc
    if page.items:
        raise ProblemError(409, "folder_not_empty", "The project folder already holds files.")
    row = await update_versioned(
        s, _folders, folder["id"], folder["version"], {"location_id": target["id"]}
    )
    return _folder_out(row)


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
    """Write a file into the project's folder; 409 `location_offline` while the location
    is offline, whether marked so or found so now, and nothing reaches it."""
    folder = await _folder_row(s, project_id)
    location = await _location_row(s, folder["location_id"])
    _require_online(location)
    try:
        rel = f"{folder['root_path']}/{safe_rel_path(path)}"
    except PathRejected as exc:
        raise _storage_problem(exc) from exc
    async with _opened(s, location, net=net, resolver=resolver) as backend:
        if (await _health(backend)).status != "ok":
            raise ProblemError(409, "location_offline", "The location is offline.")
        try:
            return await backend.write(rel, data, if_match)
        except (StorageError, AdapterError) as exc:
            raise _storage_problem(exc) from exc


async def save_note(
    s: AsyncSession,
    document_id: UUID,
    *,
    if_match: str | None = None,
    net: NetPolicy,
    resolver: Resolver = system_resolver,
) -> NoteWrite:
    """Snapshot a text document into `document_versions` and write it to
    `<folder>/notes/<document id>.md`. While the location is offline the write queues in
    `pending_writes` (the text is safe in Postgres) and lands on the next healthy check."""
    doc = (
        (
            await s.execute(
                select(_documents.c.project_id, _documents.c.body_md).where(
                    _documents.c.id == document_id, _documents.c.deleted_at.is_(None)
                )
            )
        )
        .mappings()
        .first()
    )
    if doc is None or doc["project_id"] is None:
        raise NotFound("documents", document_id)
    folder = await _folder_row(s, doc["project_id"])
    location = await _location_row(s, folder["location_id"])
    body = (doc["body_md"] or "").encode()
    digest = hashlib.sha256(body)
    last = await s.scalar(
        select(func.max(_versions.c.version_no)).where(_versions.c.document_id == document_id)
    )
    version_id = await s.scalar(
        insert(_versions)
        .values(
            document_id=document_id,
            version_no=(last or 0) + 1,
            content_hash=digest.digest(),
            body_md=doc["body_md"] or "",
            size=len(body),
        )
        .returning(_versions.c.id)
    )
    path = f"{folder['root_path']}/notes/{document_id}.md"
    written = NoteWrite(status="written", location_id=location["id"], path=path)
    queued = NoteWrite(status="queued", location_id=location["id"], path=path)
    online = location["status"] == "online"
    async with _opened(s, location, net=net, resolver=resolver) as backend:
        if online:
            health = await _health(backend)
            if health.status != "ok":
                await _set_status(s, location["id"], health)
                online = False
        if online:
            try:
                await backend.write(path, _one_chunk(body), if_match)
            except PreconditionFailed as exc:
                if not await _holds(backend, path, body, exc.current):
                    raise _storage_problem(exc) from exc
            except (LocationOffline, AdapterUnavailable) as exc:
                await _set_status(s, location["id"], Health.degraded(type(exc).__name__))
                online = False
            except (StorageError, AdapterError) as exc:
                raise _storage_problem(exc) from exc
    if online:
        return written
    await s.execute(
        insert(_pending).values(
            location_id=location["id"], path=path, document_version_id=version_id, if_match=if_match
        )
    )
    return queued


# --- Text documents edited in the app (P0-24: the project page's Brief rail) --------------


async def project_of(ctx: WorkspaceContext, document_id: UUID) -> UUID | None:
    """The document's project in `ctx`'s workspace (the `lookup:knowledge` routes, R-28)."""
    async with tenant_session(ctx) as s:
        found: UUID | None = await s.scalar(
            select(_documents.c.project_id).where(
                _documents.c.id == document_id, _documents.c.deleted_at.is_(None)
            )
        )
    return found


register_project_lookup("knowledge", project_of)


async def update_text_document(
    s: AsyncSession, document_id: UUID, *, body_md: str, version: int
) -> DocumentDTO:
    """Replace a text entry's Markdown body (versioned). Synced files and uploads are not
    edited here: anything but `kind == "text"` is 409 `not_text`."""
    kind = await s.scalar(
        select(_documents.c.kind).where(
            _documents.c.id == document_id, _documents.c.deleted_at.is_(None)
        )
    )
    if kind is None:
        raise NotFound("documents", document_id)
    if kind != "text":
        raise ProblemError(409, "not_text", "Only text entries can be edited here")
    values = {"body_md": body_md, "content_hash": hashlib.sha256(body_md.encode()).digest()}
    try:
        row = await update_versioned(s, _documents, document_id, version, values)
    except StaleVersion as exc:
        current = DocumentDTO.model_validate(dict(exc.current)).model_dump(mode="json")
        raise StaleVersion(current=current) from None
    if row["project_id"] is not None:
        mark_changed(s, "project", row["project_id"])
    return DocumentDTO.model_validate(dict(row))


# --- Uploads and files found in a folder (P1-16, SEC-10, FR-15.2, FR-15.12) --------------
#
# An upload is spooled by the route to `<spool>/<version_id>` (untrusted bytes never reach a
# location before the scan), `begin_upload` writes its rows (`pending_scan`, tainted,
# untrusted) and `enqueue_extract` starts the `knowledge_extract_document` workflow on the
# `extract` queue, which `worker-extract` alone dequeues. A file found in a project folder
# takes the same path with `source = "storage"`. Files are served back only through
# `download_info` and `stream_file`, once the document is `ready` (decision 10: the api
# process may read storage to serve them).

EXTRACT_QUEUE: Final = "extract"
EXTRACT_WORKFLOW: Final = "knowledge_extract_document"
WORKSPACE_FOLDER: Final = "workspace"  # folder root of workspace knowledge-base uploads


class UploadAccepted(BaseModel):
    id: UUID  # the document
    version_id: UUID
    status: Literal["pending_scan"]


class DocumentStatusOut(BaseModel):
    """A document's state as the upload flow polls it (P1-17 owns the full read)."""

    id: UUID
    project_id: UUID | None
    title: str
    kind: str
    path: str | None
    status: Literal["pending_scan", "extracting", "ready", "quarantined", "failed"]
    status_reason: str | None
    trust: Literal["trusted", "untrusted"]
    tainted: bool
    current_version_id: UUID | None
    version: int


async def file_location(
    s: AsyncSession, project_id: UUID | None, *, require_online: bool = False
) -> tuple[UUID, str]:
    """(location id, folder root) where a project's files live: its folder, or for the
    workspace knowledge base (no project) the `workspace` folder on the default location.
    409 `no_location` without one; with `require_online`, 409 `location_offline` too."""
    row: Row | None
    root = WORKSPACE_FOLDER
    if project_id is None:
        row = (
            (
                await s.execute(
                    select(_locations).where(
                        _locations.c.is_default, _locations.c.deleted_at.is_(None)
                    )
                )
            )
            .mappings()
            .first()
        )
    else:
        try:
            folder = await _folder_row(s, project_id)
        except NotFound:
            row = None
        else:
            row = await _location_row(s, folder["location_id"])
            root = folder["root_path"]
    if row is None:
        raise ProblemError(409, "no_location", "There is no storage location for these files yet")
    if require_online:
        _require_online(row)
    return row["id"], root


async def storage_path(s: AsyncSession, project_id: UUID | None, rel: str) -> str:
    """`rel` (relative to the project's folder) as a path on its location; "" gives the
    folder with a trailing slash."""
    _, root = await file_location(s, project_id)
    return f"{root}/{rel}"


async def check_upload_target(ctx: WorkspaceContext, project_id: UUID | None) -> None:
    """Before the body is read: the project exists (404) and its location is online (409)."""
    async with tenant_session(ctx) as s:
        if project_id is not None and not await projects.project_exists(s, project_id):
            raise NotFound("projects", project_id)
        await file_location(s, project_id, require_online=True)


async def begin_upload(
    ctx: WorkspaceContext,
    *,
    project_id: UUID | None,
    name: str,
    title: str | None,
    sha256: str,
    size: int,
    version_id: UUID,
) -> UploadAccepted:
    """The rows for an upload the route has spooled to `<spool>/<version_id>`: an untrusted,
    tainted document and its first version, both `pending_scan`."""
    digest = bytes.fromhex(sha256)
    async with tenant_session(ctx) as s:
        location_id, _ = await file_location(s, project_id)
        document_id = await store.new_document(
            s,
            project_id=project_id,
            title=title or name,
            digest=digest,
            location_id=location_id,
            path=None,
            source="upload",
        )
        await store.add_version(
            s, document_id, version_id, digest=digest, size=size, source_name=name
        )
    return UploadAccepted(id=document_id, version_id=version_id, status="pending_scan")


async def enqueue_extract(ctx: WorkspaceContext, version_id: UUID, source: str) -> None:
    """Start the version's extraction on the `extract` queue; the workflow id makes a
    repeated call return the same workflow."""
    await deadletter.dbos_client().enqueue_async(
        {
            "queue_name": EXTRACT_QUEUE,
            "workflow_name": EXTRACT_WORKFLOW,
            "workflow_id": f"extract:{version_id}",
        },
        str(ctx.workspace_id),
        str(version_id),
        source,
    )


async def ingest_folder_file(
    ctx: WorkspaceContext, *, project_id: UUID, path: str, size: int
) -> UploadAccepted:
    """The rows for a file found in the project's folder (`path` relative to the folder),
    and its extraction enqueued with `source = "storage"`. A file already known at that
    path gets a new version."""
    try:
        rel = safe_rel_path(path)
    except PathRejected as exc:
        raise _storage_problem(exc) from exc
    version_id = uuid7()
    name = PurePosixPath(rel).name
    async with tenant_session(ctx) as s:
        location_id, _ = await file_location(s, project_id)
        document_id = await store.find_by_path(s, project_id, rel)
        if document_id is None:
            document_id = await store.new_document(
                s,
                project_id=project_id,
                title=name,
                digest=store.NO_HASH,
                location_id=location_id,
                path=rel,
                source="folder",
            )
        await store.add_version(
            s, document_id, version_id, digest=store.NO_HASH, size=size, source_name=name
        )
    await enqueue_extract(ctx, version_id, "storage")
    return UploadAccepted(id=document_id, version_id=version_id, status="pending_scan")


async def get_document(s: AsyncSession, document_id: UUID) -> DocumentStatusOut:
    row = (
        (
            await s.execute(
                select(_documents).where(
                    _documents.c.id == document_id, _documents.c.deleted_at.is_(None)
                )
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound("documents", document_id)
    return DocumentStatusOut.model_validate(dict(row))


@dataclasses.dataclass(frozen=True)
class DownloadInfo:
    """What the file route needs to answer: the name to offer, the path on its location and
    the size the location reports."""

    name: str
    project_id: UUID | None
    location_id: UUID
    path: str
    size: int


async def download_info(
    ctx: WorkspaceContext, document_id: UUID, *, version_no: int | None, net: NetPolicy
) -> DownloadInfo:
    """The document's file if it may be served: 404 for a document or version that does
    not exist, 409 `not_available` unless it is `ready` (still scanning, quarantined,
    failed)."""
    async with tenant_session(ctx) as s:
        doc = (
            (
                await s.execute(
                    select(_documents).where(
                        _documents.c.id == document_id, _documents.c.deleted_at.is_(None)
                    )
                )
            )
            .mappings()
            .first()
        )
        if doc is None:
            raise NotFound("documents", document_id)
        status, source_name = doc["status"], None
        if version_no is not None:
            ver = (
                (
                    await s.execute(
                        select(_versions).where(
                            _versions.c.document_id == document_id,
                            _versions.c.version_no == version_no,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if ver is None:
                raise NotFound("document_versions", document_id)
            status, source_name = ver["status"], ver["source_name"]
        if status != "ready" or doc["path"] is None or doc["storage_location_id"] is None:
            raise ProblemError(409, "not_available", "The file is not available yet")
        if source_name is None:
            source_name = await s.scalar(
                select(_versions.c.source_name).where(_versions.c.id == doc["current_version_id"])
            )
        full = await storage_path(s, doc["project_id"], doc["path"])
        async with open_backend(s, doc["storage_location_id"], net=net) as backend:
            try:
                stat = await backend.stat(full)
            except (StorageError, AdapterError) as exc:
                raise _storage_problem(exc) from exc
    if stat is None:
        raise ProblemError(404, "not_found", "No file at that path.")
    return DownloadInfo(
        name=source_name or PurePosixPath(doc["path"]).name,
        project_id=doc["project_id"],
        location_id=doc["storage_location_id"],
        path=full,
        size=stat.size,
    )


async def stream_file(
    ctx: WorkspaceContext, info: DownloadInfo, *, net: NetPolicy
) -> AsyncIterator[bytes]:
    """The file's bytes from its location, the storage connection open for as long as the
    response streams."""
    async with (
        tenant_session(ctx) as s,
        open_backend(s, info.location_id, net=net) as backend,
    ):
        async for chunk in backend.read(info.path):
            yield chunk
