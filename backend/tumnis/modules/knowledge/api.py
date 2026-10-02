"""knowledge public functions and DTOs; the only file other modules may import.

Knowledge owns the `documents` table (P0-12 creates it with the canonical columns, R-15;
P0-17 adds bodies and roles). A knowledge connector maps provider files to
`DocumentRecord`s and stores them with `upsert_synced_documents`; text entries and uploads
have no connection or external id, which is why those columns are nullable here and the
canonical unique key is partial.
"""

import asyncio
import dataclasses
import hashlib
import hmac
import json
import os
import re
import stat
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import (
    ColumnElement,
    RowMapping,
    Select,
    Table,
    and_,
    case,
    delete,
    func,
    insert,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from tumnis.core import agent_surface, audit, deadletter, settings_store, tenancy
from tumnis.core.adapters.errors import AdapterError, AdapterRejected, AdapterUnavailable
from tumnis.core.adapters.registry import current_mode
from tumnis.core.canonical import CanonicalRecord, UpsertStats, upsert_records
from tumnis.core.clock import SystemClock
from tumnis.core.errors import ProblemError
from tumnis.core.ids import uuid7
from tumnis.core.live import mark_changed
from tumnis.core.net import NetPolicy, Resolver, SsrfBlocked, resolve_and_check, system_resolver
from tumnis.core.outbox import emit
from tumnis.core.pagination import Cursor, Page, invalid_cursor, paginate
from tumnis.core.routing import WORKSPACE_ROW, register_project_lookup
from tumnis.core.schemas import versioned
from tumnis.core.tenancy import WorkspaceContext, session_for, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.core.versioning import NotFound, StaleVersion, update_versioned
from tumnis.modules.integrations import api as integrations
from tumnis.modules.knowledge import embeddings as _embeddings
from tumnis.modules.knowledge import search, store
from tumnis.modules.knowledge.adapters.fake import FakeStorage
from tumnis.modules.knowledge.adapters.port import ChunkRow
from tumnis.modules.knowledge.adapters.s3 import (
    S3Config,
    S3Storage,
    endpoint_host_port,
    endpoint_needs_https,
    endpoint_policy,
)
from tumnis.modules.knowledge.adapters.server_path import ServerPathStorage
from tumnis.modules.knowledge.adapters.sftp import (
    ProbedKey,
    SftpStorage,
    check_private_key,
    fingerprint,
    probe_host_key,
    sftp_policy,
    sftp_root,
)
from tumnis.modules.knowledge.models import (
    Chunk,
    Document,
    DocumentVersion,
    ExtractionArtifact,
    FolderFile,
    PendingWrite,
    ProjectFolder,
    StorageLocation,
)
from tumnis.modules.knowledge.payloads import DocumentAddedV1, DocumentChangedV1
from tumnis.modules.knowledge.rules import (
    CANDIDATES,
    PASSAGE_CAP_CHARS,
    ActorKind,
    Passage,
    default_trust,
    is_network_fs,
    markdown_sections,
    passage_query,
    safe_rel_path,
    sanitize_filename,
    select_passages,
)
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
from tumnis.modules.knowledge.storage import Page as StoragePage
from tumnis.modules.knowledge.sync_rules import dedupe_name, render_note
from tumnis.modules.projects import api as projects
from tumnis.modules.tasks import api as tasks
from tumnis.seed import DocumentSeed, register_seed_writer

_documents: Table = Document.__table__  # type: ignore[assignment]

# Chunk embeddings (P3-10, R-37): other modules, the CLI and the tests reach them here.
EmbeddingModelOut = _embeddings.EmbeddingModelOut
ReembedRequest = _embeddings.ReembedRequest
create_embedding_index = _embeddings.create_embedding_index
embed_document = _embeddings.embed_document
embedding_models = _embeddings.embedding_models
set_embedding_model = _embeddings.set_embedding_model
start_reembed = _embeddings.start_reembed


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
    # P1-17: one DTO for the rail, the editor, search results and the upload flow
    label: Literal["agent"] | None  # agent-written and not yet marked trusted
    tags: list[str]
    source: str | None  # text, agent, upload, folder, link, or a connector's
    status: Literal["pending_scan", "extracting", "ready", "quarantined", "failed"]
    status_reason: str | None
    path: str | None  # a file's place in its project folder
    provider_url: str | None  # a link's URL (never fetched)
    current_version_id: UUID | None

    @model_validator(mode="before")
    @classmethod
    def _derived(cls, data: Any) -> Any:
        """A row read from `documents` carries no `label`: it is derived from the row's
        `source` and `trust`."""
        if isinstance(data, Mapping) and "label" not in data:
            agent = data.get("source") == AGENT_SOURCE and data.get("trust") != "trusted"
            data = {**data, "label": "agent" if agent else None}
        return data


def is_note(doc: Mapping[Any, Any]) -> bool:
    """A text entry written in the app (a note, the brief): kind `text` from the app
    (`source = "text"`). A file whose sniffed kind is `text` (P1-16) is not one (#99)."""
    return bool(doc["kind"] == "text" and doc["source"] == TEXT_SOURCE)


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


AGENT_SOURCE: Final = "agent"  # `source` of a text entry an agent added (`add_document`)
NOTES: Final = "notes"  # the project folder's subfolder for notes written in the app
AGENT_OUTPUTS: Final = "agent-outputs"  # ... and for what agents add (FR-15.4)


async def add_document(  # the tool's input, plus who, when and where the file goes
    s: AsyncSession,
    caller: agent_surface.Caller,
    *,
    project_id: UUID | None,
    title: str,
    body_markdown: str,
    tags: Sequence[str] = (),
    now: datetime | None = None,
    net: NetPolicy | None = None,
) -> DocumentDTO:
    """A document an agent adds (P2-08, P2-17, SAF-1, FR-15.4, FR-15.5): always untrusted
    and written by the caller (agent-written, `source` "agent", labeled `agent`), tainted
    when the caller is (`agent_surface.caller_tainted`: a tainted run's token, or a key
    with no run). The taint is stored once and never lowered; marking the document trusted
    later affects future packets, not what was already made from it. Like a text entry it
    is version 1 and searchable at once; with `net`, a project with a folder also gets the
    file `agent-outputs/<sanitized title>.md`. None for `project_id`: the workspace
    knowledge base."""
    del now  # the row's timestamps come from the database clock, as for text entries
    await _check_project(s, project_id)
    row = (
        (
            await s.execute(
                pg_insert(_documents)
                .values(
                    project_id=project_id,
                    title=title,
                    kind="text",
                    role=None,
                    body_md=body_markdown,
                    trust="untrusted",
                    tainted=agent_surface.caller_tainted(caller),
                    pinned=False,
                    tags=_clean_tags(tags),
                    content_hash=hashlib.sha256(body_markdown.encode()).digest(),
                    source=AGENT_SOURCE,
                    status="ready",
                    created_by=caller.principal.actor,
                )
                .returning(*_documents.c)
            )
        )
        .mappings()
        .one()
    )
    return await _write_text(
        s,
        row,
        body_markdown,
        expected_version=row["version"],
        net=net,
        event=DocumentAddedV1,
        by_agent=True,
        subfolder=AGENT_OUTPUTS,
    )


# --- The SSRF policy of a storage write the agent surface makes (P2-17) --------------------
#
# A REST twin knows the deployment's policy from the app's settings and sets it around the
# call; an MCP call, which has no request, falls back to the deployment's settings.

_NET: ContextVar[NetPolicy | None] = ContextVar("knowledge_net", default=None)


def deployment_net() -> NetPolicy:
    """The deployment's SSRF policy, read from the environment; without deployment
    settings (in-process tests) the default mode."""
    from pydantic import ValidationError  # noqa: PLC0415

    from tumnis.settings import Settings  # noqa: PLC0415

    try:
        return Settings().net_policy()
    except ValidationError:
        return NetPolicy(mode="self-hosted")


@contextmanager
def using_net(net: NetPolicy) -> Iterator[None]:
    """`net` for the agent-surface calls made inside (a REST twin's request)."""
    token = _NET.set(net)
    try:
        yield
    finally:
        _NET.reset(token)


def current_net() -> NetPolicy:
    """The policy `using_net` set, else the deployment's."""
    return _NET.get() or deployment_net()


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
# A location is a server path, a share (a mounted SMB or NFS folder, P3-14), an S3
# bucket/prefix or an SFTP folder; its S3 endpoint and keys, or its SFTP host, user and
# private key, are sealed in `config_enc` with the workspace data key. An SFTP location
# is saved `pending_host_key` with the key its server shows, and opened only once the user
# confirms that key's fingerprint (`confirm_host_key`); a server that later shows another
# key marks it `host_key_changed` until the user re-pins with a reason. Each project gets
# a folder (`<project id>/`) on the workspace default location when it is created. A location whose
# health is degraded (a share without its marker, an unreachable bucket) goes offline:
# uploads to it answer 409 `location_offline`, note saves (whose text is already in
# Postgres) queue in `pending_writes`, and the next healthy check drains the queue in
# insertion order. Every storage call goes through `open_backend`.

LocationKind = Literal["server_path", "s3", "share", "sftp"]
LocationStatus = Literal["online", "offline", "pending_host_key", "host_key_changed"]
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


class SftpConfigIn(BaseModel):
    host: str = Field(min_length=1, max_length=253)  # checked by the SSRF guard at every use
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=64)
    private_key: str = Field(min_length=1, max_length=16384)  # write-only, sealed, never answered


class LocationIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: LocationKind
    root: str = Field(min_length=1, max_length=1024)  # absolute path, bucket/prefix, SFTP folder
    s3: S3ConfigIn | None = None
    sftp: SftpConfigIn | None = None
    is_default: bool = False


class LocationOut(BaseModel):
    id: UUID
    name: str
    kind: str
    root: str
    endpoint: str | None  # S3 only; the keys are never answered
    status: LocationStatus
    status_reason: str | None
    is_default: bool
    capabilities: dict[str, bool]
    version: int
    host_key_sha256: str | None = None  # SFTP: the pinned host key's fingerprint
    pending_host_key_sha256: str | None = None  # SFTP: the key the server shows, to confirm


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
    if kind in ("server_path", "share") and net.mode == "hosted":
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


@dataclasses.dataclass(frozen=True)
class SftpConfig:
    """An SFTP location's sealed settings: where, who, and the private key."""

    host: str
    port: int
    username: str
    private_key: str


LocationConfig = S3Config | SftpConfig


async def _open_config(s: AsyncSession, row: Row) -> LocationConfig:
    blob = await settings_store.open_for_workspace(
        s, row["workspace_id"], row["config_enc"], aad=_aad(row["id"])
    )
    if row["kind"] == "sftp":
        return SftpConfig(**json.loads(blob))
    return _s3_config(blob)


async def _sftp_config(s: AsyncSession, row: Row) -> SftpConfig:
    config = await _open_config(s, row)
    if not isinstance(config, SftpConfig):  # pragma: no cover  # the kind decides the shape
        raise ProblemError(422, "invalid_location", "The SFTP settings are missing.")
    return config


def _aad(location_id: UUID) -> bytes:
    return f"storage_locations:{location_id}".encode()


class _Unopened:
    """The backend of an SFTP location whose host key is not pinned (or no longer
    matches): nothing connects, every write is `LocationOffline`, health says why."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def _offline(self) -> LocationOffline:
        return LocationOffline(self.reason)

    async def stat(self, path: str) -> FileStat | None:
        raise self._offline()

    async def list(self, prefix: str, cursor: str | None) -> StoragePage[FileStat]:
        raise self._offline()

    async def read(self, path: str) -> AsyncIterator[bytes]:
        raise self._offline()
        yield b""  # pragma: no cover  # makes this an async generator

    async def write(self, path: str, data: AsyncIterator[bytes], if_match: str | None) -> FileStat:
        raise self._offline()

    async def move(self, src: str, dst: str) -> None:
        raise self._offline()

    async def delete(self, path: str) -> None:
        raise self._offline()

    async def ensure_folder(self, path: str) -> None:
        raise self._offline()

    async def health(self) -> Health:
        return Health.degraded(self.reason)


def _sftp_storage(
    row: Row, config: SftpConfig, pinned: str, *, net: NetPolicy, resolver: Resolver
) -> SftpStorage:
    return SftpStorage(
        host=config.host,
        port=config.port,
        username=config.username,
        private_key_pem=config.private_key.encode(),
        pinned_host_key=pinned,
        root=row["root"],
        net_policy=net,
        resolver=resolver,
    )


def _backend(
    row: Row,
    config: LocationConfig | None,
    *,
    net: NetPolicy,
    resolver: Resolver,
) -> StorageBackend:
    _refuse_hosted_server_path(row["kind"], net)
    if current_mode() == "fake":
        return _FAKES.setdefault(row["id"], FakeStorage())
    caps = row["capabilities"] or {}
    if row["kind"] in ("server_path", "share"):
        return ServerPathStorage(
            row["root"], kind=row["kind"], network_fs=bool(caps.get("network_fs"))
        )
    if row["kind"] == "s3" and isinstance(config, S3Config):
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
    if row["kind"] == "sftp" and isinstance(config, SftpConfig):
        pinned = row.get("host_key_pinned")
        status = row.get("status")
        if not pinned or status in ("pending_host_key", "host_key_changed"):
            return _Unopened(status or "pending_host_key")
        return _sftp_storage(row, config, pinned, net=net, resolver=resolver)
    raise ProblemError(422, "invalid_location", f"{row['kind']} locations are not supported yet")


async def _close(backend: StorageBackend) -> None:
    if isinstance(backend, S3Storage | SftpStorage):
        await backend.aclose()


BackendHook = Callable[[Row, StorageBackend], StorageBackend]
_backend_hook: list[BackendHook | None] = [None]


def use_backend_hook(fn: BackendHook | None) -> None:
    """Wrap every backend `open_backend` builds (tests: a write log, a counting store);
    None removes the hook."""
    _backend_hook[0] = fn


@asynccontextmanager
async def _opened(
    s: AsyncSession, row: RowMapping, *, net: NetPolicy, resolver: Resolver
) -> AsyncIterator[StorageBackend]:
    config = await _open_config(s, row) if row["config_enc"] is not None else None
    built = _backend(row, config, net=net, resolver=resolver)
    hook = _backend_hook[0]
    try:
        yield hook(row, built) if hook is not None else built
    finally:
        await _close(built)


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


def _sha256_of(openssh: str | None) -> str | None:
    return fingerprint(openssh) if openssh else None


async def _location_out(s: AsyncSession, row: Row) -> LocationOut:
    endpoint = None
    if row["config_enc"] is not None:
        config = await _open_config(s, row)
        if isinstance(config, S3Config):
            endpoint = config.endpoint
        else:
            endpoint = f"{config.username}@{config.host}:{config.port}"
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
        host_key_sha256=_sha256_of(row.get("host_key_pinned")),
        pending_host_key_sha256=_sha256_of(row.get("host_key_pending")),
    )


class HostKeyReviewPayload(BaseModel):
    """An SFTP server showed a host key other than the pinned one: `accept` re-pins
    through the confirm flow (the fingerprint typed again, with a reason)."""

    location_id: UUID
    pinned_sha256: str | None = None


HOST_KEY_REVIEW_KIND: Final = "storage_host_key_changed"
tasks.register_review_kind(
    tasks.ReviewKindSpec(
        kind=HOST_KEY_REVIEW_KIND,
        owner_module="knowledge",
        payload_schema=HostKeyReviewPayload,
        actions=("accept", "snooze"),
        impact_scope="workspace",
    )
)


async def _host_key_mismatch(s: AsyncSession, row: Row) -> None:
    """Audit `storage.host_key_mismatch` and queue one `storage_host_key_changed` review
    item (deduplicated per location while it is open)."""
    pinned = _sha256_of(row.get("host_key_pinned"))
    await audit.record(
        s,
        "storage.host_key_mismatch",
        target=("storage_locations", row["id"]),
        details={"pinned_sha256": pinned},
        occurred_at=SystemClock().now(),
    )
    await tasks.add_review_item(
        HOST_KEY_REVIEW_KIND,
        target=tasks.TargetRef(type="storage_location", id=row["id"]),
        project_id=None,
        payload=HostKeyReviewPayload(location_id=row["id"], pinned_sha256=pinned).model_dump(
            mode="json"
        ),
        dedupe_key=f"{HOST_KEY_REVIEW_KIND}:{row['id']}",
        session=s,
    )


async def _set_status(s: AsyncSession, location_id: UUID, health: Health) -> RowMapping:
    """The location's status as its health says. A changed SFTP host key is sticky
    (`host_key_changed`): it is audited and put in the review queue once, and the location
    is not opened again until the user re-pins."""
    online = health.status == "ok"
    status = "online" if online else "offline"
    if health.reason == "host_key_changed":
        status = "host_key_changed"
        before = await _location_row(s, location_id)
        if before["status"] != "host_key_changed":
            await _host_key_mismatch(s, before)
    stmt = (
        update(_locations)
        .where(_locations.c.id == location_id)
        .values(status=status, status_reason=None if online else health.reason)
        .returning(*_locations.c)
    )
    return (await s.execute(stmt)).mappings().one()


async def _clear_default(s: AsyncSession, keep: UUID | None) -> None:
    stmt = update(_locations).where(_locations.c.is_default, _locations.c.deleted_at.is_(None))
    if keep is not None:
        stmt = stmt.where(_locations.c.id != keep)
    await s.execute(stmt.values(is_default=False))


def _share_marker_present(root: str) -> bool:
    """The user's `.tumnis-root` marker is a regular file at the share's root (a share
    that is not mounted shows the bare mount point, which has none)."""
    try:
        return stat.S_ISREG(os.lstat(Path(root) / ServerPathStorage.MARKER).st_mode)
    except OSError:
        return False


async def _probe(config: SftpConfig, net: NetPolicy, resolver: Resolver) -> ProbedKey:
    """The host key the SFTP server shows now, through the SSRF guard (422 `ssrf_blocked`,
    nothing sent) with a short timeout (422 `host_unreachable`)."""
    try:
        return await probe_host_key(config.host, config.port, net_policy=net, resolver=resolver)
    except SsrfBlocked as exc:
        raise _storage_problem(exc) from exc
    except AdapterError as exc:
        raise ProblemError(422, "host_unreachable", "The SFTP server did not answer.") from exc


async def _check_sftp_host(config: SftpConfig, net: NetPolicy, resolver: Resolver) -> None:
    """The SFTP host against the SSRF guard (its name may resolve elsewhere now)."""
    try:
        await resolve_and_check(config.host, config.port, sftp_policy(net, config.port), resolver)
    except SsrfBlocked as exc:
        raise _storage_problem(exc) from exc
    except AdapterError as exc:
        raise ProblemError(422, "invalid_location", "The host does not resolve.") from exc


def _sftp_body(body: LocationIn) -> tuple[str, SftpConfig]:
    """(the normalized root, the settings) of an SFTP location form; 422 when unusable."""
    if body.sftp is None:
        raise ProblemError(422, "invalid_location", "An SFTP location needs its host.")
    try:
        root = sftp_root(body.root)
    except PathRejected as exc:
        raise ProblemError(422, "invalid_location", "The SFTP folder is not a safe path.") from exc
    try:
        check_private_key(body.sftp.private_key.encode())
    except ValueError as exc:
        raise ProblemError(
            422,
            "invalid_location",
            "The private key is not an OpenSSH or PEM key without a passphrase.",
        ) from exc
    return root, SftpConfig(**body.sftp.model_dump())


async def create_location(  # one branch per kind
    s: AsyncSession, body: LocationIn, *, net: NetPolicy, resolver: Resolver = system_resolver
) -> LocationOut:
    """Save a location after checking it: the root's shape, the S3 endpoint or SFTP host
    against the SSRF guard (422 `ssrf_blocked`, nothing saved), then its health and
    capabilities (a share's filesystem type; whether the provider honours conditional
    puts). A share needs its `.tumnis-root` marker (422 `marker_missing`, nothing saved).
    An SFTP location is saved `pending_host_key` with the key its server shows; it opens
    once `confirm_host_key` pins it. The first location, or one saved with `is_default`,
    becomes the workspace default."""
    location_id = uuid7()
    taken = await s.scalar(
        select(_locations.c.id).where(
            func.lower(_locations.c.name) == body.name.lower(), _locations.c.deleted_at.is_(None)
        )
    )
    if taken is not None:
        raise ProblemError(409, "name_taken", "A location with that name exists.")
    config: LocationConfig | None = None
    probed: ProbedKey | None = None
    _refuse_hosted_server_path(body.kind, net)
    if body.kind == "server_path":
        root = _server_root(body.root)
        fstype = _fstype(root)
        caps = {"network_fs": fstype is not None and is_network_fs(fstype)}
    elif body.kind == "share":
        root = _server_root(body.root)
        if not await asyncio.to_thread(_share_marker_present, root):
            raise ProblemError(
                422, "marker_missing", "Place a .tumnis-root file at the share's root first."
            )
        caps = {"network_fs": True}
    elif body.kind == "sftp":
        root, config = _sftp_body(body)
        probed = await _probe(config, net, resolver)
        caps = {}
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
    if probed is not None:
        health = Health.degraded("pending_host_key")
        row |= {"host_key_pending": probed.openssh}
    else:
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
    status = "pending_host_key" if probed is not None else "online" if online else "offline"
    row |= {
        "capabilities": caps,
        "is_default": body.is_default or has_default is None,
        "status": status,
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
    config: LocationConfig | None,
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
        await _close(backend)
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
    location drains its queued writes. An S3 endpoint or SFTP host passes the SSRF guard
    again first (its name may resolve elsewhere now, or the deployment may run hosted): a
    blocked one is refused with 422 `ssrf_blocked`, and the location is not opened.

    An SFTP location whose key is not pinned, or whose server showed another key, is never
    opened: the test shows the key the server presents now (`pending_host_key_sha256`) and
    never pins it. A pinned location that meets another key becomes `host_key_changed`."""
    row = await _location_row(s, location_id)
    if row["kind"] == "s3" and row["config_enc"] is not None:
        config = await _open_config(s, row)
        if isinstance(config, S3Config):
            await _check_endpoint(config, net, resolver)
    if row["kind"] == "sftp":
        sftp = await _sftp_config(s, row)
        await _check_sftp_host(sftp, net, resolver)
        if row["status"] in ("pending_host_key", "host_key_changed"):
            return await _location_out(s, await _show_key(s, row, sftp, net, resolver))
    async with _opened(s, row, net=net, resolver=resolver) as backend:
        health = await _health(backend)
        row = await _set_status(s, location_id, health)
        if health.status == "ok":
            await _drain(s, location_id, backend)
    if row["status"] == "host_key_changed":
        row = await _show_key(s, row, await _sftp_config(s, row), net, resolver)
    return await _location_out(s, row)


async def _show_key(
    s: AsyncSession, row: Row, config: SftpConfig, net: NetPolicy, resolver: Resolver
) -> RowMapping:
    """Record the host key the server shows now as the one to confirm (nothing is pinned);
    a server that does not answer leaves the last one shown."""
    try:
        probed = await probe_host_key(config.host, config.port, net_policy=net, resolver=resolver)
    except SsrfBlocked as exc:
        raise _storage_problem(exc) from exc
    except AdapterError:
        return await _location_row(s, row["id"])
    stmt = (
        update(_locations)
        .where(_locations.c.id == row["id"])
        .values(host_key_pending=probed.openssh)
        .returning(*_locations.c)
    )
    return (await s.execute(stmt)).mappings().one()


async def confirm_host_key(  # the confirm form, the net policy
    s: AsyncSession,
    location_id: UUID,
    sha256: str,
    *,
    reason: str | None = None,
    net: NetPolicy,
    resolver: Resolver = system_resolver,
) -> LocationOut:
    """Pin the SFTP host key the server shows now, when `sha256` is exactly its
    fingerprint (`SHA256:…`, as `ssh-keygen -lf` prints it); anything else is 422
    `fingerprint_mismatch` and pins nothing. Re-pinning after a changed key needs a reason
    (422 `reason_required`). Audited as `storage.host_key_pinned` (with the reason); the
    location then comes online when it answers, and its queued writes drain."""
    row = await _location_row(s, location_id)
    if row["kind"] != "sftp":
        raise ProblemError(422, "invalid_location", "Only SFTP locations have a host key.")
    repin = row["host_key_pinned"] is not None
    if repin and not (reason and reason.strip()):
        raise ProblemError(422, "reason_required", "Say why the server's key changed.")
    config = await _sftp_config(s, row)
    probed = await _probe(config, net, resolver)
    if not hmac.compare_digest(sha256.strip().encode(), probed.sha256.encode()):
        # The key shown now is kept for the form even though the request fails: in its own
        # transaction, as the caller's rolls back with the 422.
        pending = (
            update(_locations)
            .where(_locations.c.id == location_id)
            .values(host_key_pending=probed.openssh)
        )
        ctx = tenancy.current()
        if ctx is None:
            await s.execute(pending)
        else:
            async with tenant_session(ctx) as own:
                await own.execute(pending)
        raise ProblemError(
            422, "fingerprint_mismatch", "That fingerprint is not the server's host key."
        )
    backend = _sftp_storage(row, config, probed.openssh, net=net, resolver=resolver)
    try:
        health = await _health(backend)
        await audit.record(
            s,
            "storage.host_key_pinned",
            target=("storage_locations", location_id),
            reason=reason.strip() if reason else None,
            details={
                "sha256": probed.sha256,
                "previous_sha256": _sha256_of(row["host_key_pinned"]),
            },
            occurred_at=SystemClock().now(),
        )
        online = health.status == "ok"
        saved = (
            (
                await s.execute(
                    update(_locations)
                    .where(_locations.c.id == location_id)
                    .values(
                        host_key_pinned=probed.openssh,
                        host_key_pending=None,
                        status="online" if online else "offline",
                        status_reason=None if online else health.reason,
                    )
                    .returning(*_locations.c)
                )
            )
            .mappings()
            .one()
        )
        if online:
            await _drain(s, location_id, backend)
    finally:
        await backend.aclose()
    return await _location_out(s, saved)


async def _drain(s: AsyncSession, location_id: UUID, backend: StorageBackend) -> None:
    """Write the location's queued notes in insertion order, deleting each row once its
    bytes are there. A precondition failure whose current content is the queued content
    counts as landed (a crash between the write and the delete); any other is a conflict
    left queued for the sync engine (P1-15). Rows for one path chain: each after the first
    takes the etag the previous one left, since they were queued against the same file.
    An outage stops the drain.

    Each landed note is recorded in `folder_files` (P1-15), synced at the Document's
    version when its body is still the queued one, else at 0 so the next sync writes the
    newer body through."""
    queued = (
        (
            await s.execute(
                select(
                    _pending,
                    _versions.c.body_md,
                    _versions.c.document_id,
                    _documents.c.body_md.label("current_body"),
                    _documents.c.version.label("doc_version"),
                )
                .join(_versions, _versions.c.id == _pending.c.document_version_id)
                .join(_documents, _documents.c.id == _versions.c.document_id)
                .where(_pending.c.location_id == location_id, _pending.c.deleted_at.is_(None))
                .order_by(_pending.c.created_at, _pending.c.id)
            )
        )
        .mappings()
        .all()
    )
    landed: dict[str, str] = {}  # path -> etag an earlier row of this drain left there
    for item in queued:
        body = render_note(item["document_id"], item["body_md"]).encode()
        path = item["path"]
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
        current = item["current_body"] == item["body_md"]
        await record_file(
            s,
            location_id,
            written,
            content_hash=hashlib.sha256(body).hexdigest(),
            origin="tumnis",
            document_id=item["document_id"],
            synced_version=item["doc_version"] if current else 0,
            last_op="drain",
        )


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


_FOLDER_NAME_LOCK: Final = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")


async def assign_project_folder(s: AsyncSession, project_id: UUID) -> ProjectFolderOut | None:
    """The project's folder on the workspace default location, named after the project
    (sanitized, and numbered when the location already has a folder of that name; P1-15),
    once: a second call finds it and writes nothing. None while the workspace has no
    default. Records the folder only; `ensure_project_folder` also makes it."""
    default = await s.scalar(
        select(_locations.c.id).where(_locations.c.is_default, _locations.c.deleted_at.is_(None))
    )
    if default is None:
        return None
    existing = await s.scalar(select(_folders.c.id).where(_folders.c.project_id == project_id))
    if existing is None:
        # One naming at a time per location: a concurrent project.created waits here, then
        # sees this folder's name as taken (the unique key covers the project, not the name).
        await s.execute(_FOLDER_NAME_LOCK, {"key": f"project-folder-name:{default}"})
        await s.execute(
            pg_insert(_folders)
            .values(
                project_id=project_id,
                location_id=default,
                root_path=await _folder_name(s, project_id, default),
            )
            .on_conflict_do_nothing(index_elements=[_folders.c.workspace_id, _folders.c.project_id])
        )
    return await get_project_folder(s, project_id)


async def _folder_name(s: AsyncSession, project_id: UUID, location_id: UUID) -> str:
    """`sanitize_filename(project name)`, numbered past the location's other folders
    (compared ignoring case; deleted folders count, their files may still be there)."""
    name = (await projects.get_project(s, project_id)).name
    taken: list[str] = list(
        await s.scalars(select(_folders.c.root_path).where(_folders.c.location_id == location_id))
    )
    return dedupe_name(sanitize_filename(name), frozenset(p.casefold() for p in taken))


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


async def save_note(  # noqa: PLR0912  # queue, write and record, one branch each
    s: AsyncSession,
    document_id: UUID,
    *,
    if_match: str | None = None,
    net: NetPolicy,
    resolver: Resolver = system_resolver,
    version_id: UUID | None = None,
    subfolder: str = NOTES,
) -> NoteWrite:
    """Snapshot a text document into `document_versions` (unless the caller made the
    version already and names it, P1-17) and write it to its file: the
    path its `folder_files` record names, else `<folder>/notes/<sanitized title>.md`
    (`subfolder`: `agent-outputs` for what an agent adds; numbered when taken). The file is
    the body under `tumnis_id` frontmatter (P1-15, Scott's decision 15), so the note keeps
    its identity when renamed outside; without `if_match` a recorded file is replaced only
    while it still holds what was last synced. While the location is offline the write
    queues in `pending_writes` (the text is safe in Postgres) and lands on the next healthy
    check."""
    doc = (
        (
            await s.execute(
                select(
                    _documents.c.project_id,
                    _documents.c.body_md,
                    _documents.c.title,
                    _documents.c.version,
                ).where(_documents.c.id == document_id, _documents.c.deleted_at.is_(None))
            )
        )
        .mappings()
        .first()
    )
    if doc is None or doc["project_id"] is None:
        raise NotFound("documents", document_id)
    folder = await _folder_row(s, doc["project_id"])
    location = await _location_row(s, folder["location_id"])
    text_body = doc["body_md"] or ""
    body = render_note(document_id, text_body).encode()
    if version_id is None:
        last = await s.scalar(
            select(func.max(_versions.c.version_no)).where(_versions.c.document_id == document_id)
        )
        version_id = await s.scalar(
            insert(_versions)
            .values(
                document_id=document_id,
                version_no=(last or 0) + 1,
                content_hash=hashlib.sha256(text_body.encode()).digest(),
                body_md=text_body,
                size=len(text_body.encode()),
            )
            .returning(_versions.c.id)
        )
    record = await file_record_of(s, location["id"], document_id)
    if record is not None and if_match is None:
        if_match = record["etag"]
    online = location["status"] == "online"
    async with _opened(s, location, net=net, resolver=resolver) as backend:
        if online:
            health = await _health(backend)
            if health.status != "ok":
                await _set_status(s, location["id"], health)
                online = False
        if record is not None:
            path = record["path"]
        else:
            path = await note_path(
                s,
                location["id"],
                folder["root_path"],
                doc["title"],
                document_id=document_id,
                backend=backend if online else None,
                subfolder=subfolder,
            )
        landed: FileStat | None = None
        if online:
            try:
                landed = await backend.write(path, _one_chunk(body), if_match)
            except PreconditionFailed as exc:
                if not await _holds(backend, path, body, exc.current):
                    raise _storage_problem(exc) from exc
                landed = exc.current
            except (LocationOffline, AdapterUnavailable) as exc:
                await _set_status(s, location["id"], Health.degraded(type(exc).__name__))
                online = False
            except (StorageError, AdapterError) as exc:
                raise _storage_problem(exc) from exc
    if landed is not None:
        await record_file(
            s,
            location["id"],
            landed,
            content_hash=hashlib.sha256(body).hexdigest(),
            origin="tumnis",
            document_id=document_id,
            synced_version=doc["version"],
            last_op="save_note",
        )
        return NoteWrite(status="written", location_id=location["id"], path=path)
    await s.execute(
        insert(_pending).values(
            location_id=location["id"], path=path, document_version_id=version_id, if_match=if_match
        )
    )
    return NoteWrite(status="queued", location_id=location["id"], path=path)


# --- Text documents edited in the app (P0-24: the project page's Brief rail) --------------


async def project_of(ctx: WorkspaceContext, document_id: UUID) -> UUID | None:
    """The document's project in `ctx`'s workspace (the `lookup:knowledge` routes, R-28),
    trashed or not (restore names a trashed one; the handlers answer 404 for the rest)."""
    async with tenant_session(ctx) as s:
        found: UUID | None = await s.scalar(
            select(_documents.c.project_id).where(_documents.c.id == document_id)
        )
    return found


register_project_lookup("knowledge", project_of)


async def readable_scope(ctx: WorkspaceContext, document_id: UUID) -> UUID | None:
    """Where a live document is read from (the `lookup:knowledge_read` route and the
    `get_document` tool, R-28): its project, `WORKSPACE_ROW` for the workspace knowledge
    base (which every project's caller may read, FR-15.1), None when there is no such live
    document. Writes keep `project_of`, so the knowledge base stays closed to them."""
    async with tenant_session(ctx) as s:
        found = (
            await s.execute(
                select(_documents.c.project_id).where(
                    _documents.c.id == document_id, _documents.c.deleted_at.is_(None)
                )
            )
        ).first()
    if found is None:
        return None
    project_id: UUID | None = found.project_id
    return WORKSPACE_ROW if project_id is None else project_id


register_project_lookup("knowledge_read", readable_scope)


async def citation_title(s: AsyncSession, document_id: UUID, project_id: UUID) -> str | None:
    """The title of a live document a run of `project_id` may cite (P2-17, FR-15.4): one of
    that project's or of the workspace knowledge base; None for any other."""
    found = (
        await s.execute(
            select(_documents.c.title, _documents.c.project_id).where(
                _documents.c.id == document_id, _documents.c.deleted_at.is_(None)
            )
        )
    ).first()
    if found is None or found.project_id not in (None, project_id):
        return None
    title: str = found.title
    return title


async def update_text_document(
    s: AsyncSession,
    document_id: UUID,
    *,
    body_md: str,
    version: int,
    net: NetPolicy | None = None,
) -> DocumentDTO:
    """Replace a text entry's Markdown body (versioned): `update_text_entry` (P1-17), the
    name P0-24's Brief rail calls."""
    return await update_text_entry(s, document_id, body_md, expected_version=version, net=net)


# --- Project folders and the sync engine's records (P1-15, FR-15.12, FR-15.6, REL-1) ------
#
# A Tumnis-made project folder holds `uploads/`, `notes/`, `agent-outputs/` and Tumnis's own
# `.tumnis/` (its trash). `folder_files` records every file the sync engine (`sync.py`,
# the `knowledge_folder_sync` workflow) knows on a location: its state at the last sync,
# who made it and the Document it is linked to. Everything Tumnis writes into a folder
# (note saves, uploads, the engine's own writes) is recorded, so the next scan never takes
# it for an outside file.

FOLDER_LAYOUT: Final = ("uploads", "notes", "agent-outputs", ".tumnis")
TUMNIS_DIR: Final = ".tumnis"
EXISTING_DIR: Final = "Tumnis"  # an existing folder: Tumnis writes only in here (P3-14)
EXISTING_LAYOUT: Final = ("uploads", "notes", "agent-outputs", ".trash")
FOLDER_SOURCE: Final = "folder"  # documents.source of a Document made from a folder file
UPLOAD_SOURCE: Final = "upload"  # ... of an upload Tumnis placed in the folder
SYNC_REVIEW_KINDS: Final = (
    "sync_conflict",
    "deleted_outside_edited_inside",
    "edited_outside_deleted_inside",
)

_files: Table = FolderFile.__table__  # type: ignore[assignment]


class SyncReviewPayload(BaseModel):
    """A folder sync decision waiting for the user: `accept` keeps Tumnis's version, `edit`
    the folder's, `reject` keeps both."""

    location_id: UUID
    path: str
    conflict_path: str | None = None
    document_id: UUID | None = None


for _kind in SYNC_REVIEW_KINDS:
    tasks.register_review_kind(
        tasks.ReviewKindSpec(
            kind=_kind,
            owner_module="knowledge",
            payload_schema=SyncReviewPayload,
            actions=("accept", "edit", "reject", "snooze"),
            impact_scope="project",
        )
    )


async def record_file(  # one keyword per column
    s: AsyncSession,
    location_id: UUID,
    stat: FileStat,
    *,
    content_hash: str,
    origin: Literal["tumnis", "external"],
    document_id: UUID | None,
    synced_version: int | None,
    last_op: str,
) -> None:
    """Insert or replace the record of `stat.path` on the location."""
    values = {
        "size": stat.size,
        "mtime": stat.mtime,
        "etag": stat.etag,
        "content_hash": content_hash,
        "origin": origin,
        "document_id": document_id,
        "synced_version": synced_version,
        "last_op": last_op,
    }
    stmt = pg_insert(_files).values(location_id=location_id, path=stat.path, **values)
    await s.execute(
        stmt.on_conflict_do_update(
            index_elements=[_files.c.workspace_id, _files.c.location_id, _files.c.path],
            set_=values,
        )
    )


async def record_placed(
    s: AsyncSession, location_id: UUID, stat: FileStat, *, document_id: UUID, sha256: str
) -> None:
    """Record a file P1-16's pipeline placed (step 3b) as Tumnis's own, synced at the
    document's version, so the folder sync never takes it for an outside file (#99)."""
    version = await s.scalar(select(_documents.c.version).where(_documents.c.id == document_id))
    await record_file(
        s,
        location_id,
        stat,
        content_hash=sha256,
        origin="tumnis",
        document_id=document_id,
        synced_version=version,
        last_op="place_upload",
    )


async def file_record_of(
    s: AsyncSession, location_id: UUID, document_id: UUID
) -> RowMapping | None:
    """The live record of the Document's file on the location, if it has one."""
    row: RowMapping | None = (
        (
            await s.execute(
                select(_files)
                .where(
                    _files.c.location_id == location_id,
                    _files.c.document_id == document_id,
                    _files.c.deleted_at.is_(None),
                )
                .order_by(_files.c.id)
            )
        )
        .mappings()
        .first()
    )
    return row


async def taken_paths(s: AsyncSession, location_id: UUID) -> set[str]:
    """Every path the location's records and queued writes hold, casefolded."""
    recorded: list[str] = list(
        await s.scalars(select(_files.c.path).where(_files.c.location_id == location_id))
    )
    queued: list[str] = list(
        await s.scalars(
            select(_pending.c.path).where(
                _pending.c.location_id == location_id, _pending.c.deleted_at.is_(None)
            )
        )
    )
    return {p.casefold() for p in (*recorded, *queued)}


async def free_path(
    path: str, taken: set[str], backend: StorageBackend | None, *, tries: int = 1000
) -> str:
    """`path`, or its first numbered name that no taken name holds, nor a file (when the
    location answers); the name found joins `taken`."""
    for _ in range(tries):
        candidate = dedupe_name(path, frozenset(taken))
        taken.add(candidate.casefold())
        if backend is None or await backend.stat(candidate) is None:
            return candidate
    raise ProblemError(409, "name_taken", "No free name for that file.")


async def note_path(  # the note's place, its folder and where to look
    s: AsyncSession,
    location_id: UUID,
    root_path: str,
    title: str,
    *,
    document_id: UUID,
    backend: StorageBackend | None,
    taken: set[str] | None = None,
    subfolder: str = NOTES,
) -> str:
    """Where a note without a file record goes: the path of a write already queued for it,
    else `<folder>/notes/<sanitized title>.md` (or another subfolder of the project folder,
    `subfolder`), numbered past every taken name."""
    queued: str | None = await s.scalar(
        select(_pending.c.path)
        .join(_versions, _versions.c.id == _pending.c.document_version_id)
        .where(
            _pending.c.location_id == location_id,
            _pending.c.deleted_at.is_(None),
            _versions.c.document_id == document_id,
        )
        .order_by(_pending.c.created_at, _pending.c.id)
        .limit(1)
    )
    if queued is not None:
        return queued
    busy = taken if taken is not None else await taken_paths(s, location_id)
    return await free_path(f"{root_path}/{subfolder}/{sanitize_filename(title)}.md", busy, backend)


async def ensure_project_folder(
    s: AsyncSession, project_id: UUID, *, net: NetPolicy, resolver: Resolver = system_resolver
) -> ProjectFolderOut | None:
    """The project's folder (`assign_project_folder`), made on the location with its
    layout (`uploads/`, `notes/`, `agent-outputs/`, `.tumnis/`) when Tumnis made it. Once:
    making it again changes nothing, and no file is written. An offline or failing
    location keeps the record; the next folder sync makes the layout. None while the
    workspace has no default location."""
    folder = await assign_project_folder(s, project_id)
    if folder is None or folder.mode != "tumnis_made":
        return folder
    location = await _location_row(s, folder.location_id)
    if location["status"] != "online":
        return folder
    try:
        async with _opened(s, location, net=net, resolver=resolver) as backend:
            await make_layout(backend, folder.root_path)
    except (StorageError, AdapterError):
        pass  # made by the next folder sync, once the location answers
    return folder


def announce(s: AsyncSession | Session, project_id: UUID | None, document_id: UUID) -> None:
    """A knowledge write's live message (R-05): a project's item refreshes that project's
    Knowledge rail (`project`); a workspace knowledge base item, which has no project,
    sends `knowledge` with its id, so workspace lists, search and quota refresh too."""
    if project_id is not None:
        mark_changed(s, "project", project_id)
    else:
        mark_changed(s, "knowledge", document_id)


async def make_layout(backend: StorageBackend, root_path: str) -> None:
    """A Tumnis-made folder's subfolders (a no-op on S3)."""
    for sub in FOLDER_LAYOUT:
        await backend.ensure_folder(f"{root_path}/{sub}")


async def trash_document(s: AsyncSession, document_id: UUID) -> None:
    """Send a Document to Tumnis's trash. Its file is left to the folder sync: a file
    Tumnis made moves to `.tumnis/trash/`, an outside file is only unindexed."""
    project_id = await s.scalar(
        update(_documents)
        .where(_documents.c.id == document_id, _documents.c.deleted_at.is_(None))
        .values(deleted_at=func.now())
        .returning(_documents.c.project_id)
    )
    found = await s.scalar(select(_documents.c.id).where(_documents.c.id == document_id))
    if found is None:
        raise NotFound("documents", document_id)
    announce(s, project_id, document_id)


def text_of(data: bytes) -> str | None:
    """A file's bytes as text when they are UTF-8 without NULs (a stand-in body until
    extraction, P1-16); None otherwise."""
    try:
        text_body = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return None if "\x00" in text_body else text_body


async def add_version(  # the version's columns
    s: AsyncSession,
    document_id: UUID,
    data: bytes,
    body_md: str | None,
    *,
    status: Literal["pending_scan", "ready"] = "pending_scan",
    source_name: str | None = None,
) -> UUID:
    """The Document's next version: `data`'s hash and size, and its text. A file's version
    starts `pending_scan` and is released by P1-16's pipeline only (#99); `status="ready"`
    is for a note's own text, which is never served as a file."""
    last = await s.scalar(
        select(func.max(_versions.c.version_no)).where(_versions.c.document_id == document_id)
    )
    version_id: UUID = await s.scalar(
        insert(_versions)
        .values(
            document_id=document_id,
            version_no=(last or 0) + 1,
            content_hash=hashlib.sha256(data).digest(),
            body_md=body_md or "",
            size=len(data),
            status=status,
            source_name=source_name,
        )
        .returning(_versions.c.id)
    )
    return version_id


async def latest_version_hash(s: AsyncSession, document_id: UUID) -> bytes | None:
    """The sha256 of the Document's latest version, None when it has none."""
    latest = await s.scalar(
        select(_versions.c.content_hash)
        .where(_versions.c.document_id == document_id)
        .order_by(_versions.c.version_no.desc())
        .limit(1)
    )
    return bytes(latest) if latest is not None else None


async def create_file_document(  # the Document's columns
    s: AsyncSession,
    project_id: UUID,
    location_id: UUID,
    path: str,
    data: bytes,
    *,
    source: str,
    tainted: bool = True,
) -> tuple[UUID, int, UUID]:
    """A Document for the file at `path` on the location, in the project's folder: (id,
    version, first version id). Files are untrusted; outside ones tainted (FR-15.5). The
    document and its version start `pending_scan` (#99): nothing is served until P1-16's
    pipeline has scanned the file, so the caller requests its extraction with
    `source = "storage"` once this commits. `documents.path` is relative to the project
    folder, as the pipeline and `download_info` read it."""
    body = text_of(data)
    name = PurePosixPath(path).name
    row = (
        await s.execute(
            insert(_documents)
            .values(
                project_id=project_id,
                title=name,
                kind="file",
                trust="untrusted",
                tainted=tainted,
                storage_location_id=location_id,
                path=await folder_rel_path(s, project_id, path),
                body_md=body,
                content_hash=hashlib.sha256(data).digest(),
                source=source,
                status="pending_scan",
            )
            .returning(_documents.c.id, _documents.c.version)
        )
    ).one()
    version_id = await add_version(s, row.id, data, body, source_name=name)
    mark_changed(s, "project", project_id)
    return row.id, row.version, version_id


async def folder_rel_path(s: AsyncSession, project_id: UUID, path: str) -> str:
    """`path` on the location, relative to the project's folder (how `documents.path`
    holds a file's place)."""
    _, root = await file_location(s, project_id)
    if not path.startswith(root + "/"):
        raise ValueError(f"{path!r} is not inside the project folder {root!r}")
    return path[len(root) + 1 :]


class UploadPlaced(BaseModel):
    document_id: UUID
    version_id: UUID  # `pending_scan`: its extraction releases it (#99)
    location_id: UUID
    path: str  # relative to the location's root


async def place_upload(  # the upload and where it goes
    s: AsyncSession,
    project_id: UUID,
    name: str,
    data: AsyncIterator[bytes],
    *,
    net: NetPolicy,
    resolver: Resolver = system_resolver,
) -> UploadPlaced:
    """Place an upload at `uploads/<sanitized name>` in the project's folder (numbered when
    the name is taken, ignoring case), create-only, as a Document with its first version,
    recorded so the folder sync takes it for Tumnis's own. 409 `location_offline` while
    the location is offline.

    The document is `pending_scan` and never served until P1-16's pipeline releases it
    (#99): once the caller's transaction commits, it enqueues the extraction with
    `enqueue_extract(ctx, placed.version_id, "storage")` (scanned where it lies, never
    placed again). The file is written before the scan, unlike the upload route's
    spool-first path, so an infected file stays in the folder, quarantined."""
    folder = await _folder_row(s, project_id)
    location = await _location_row(s, folder["location_id"])
    _require_online(location)
    try:
        body = await spool(data)
    except TooLarge as exc:
        raise _storage_problem(exc) from exc
    wanted = f"{folder['root_path']}/uploads/{sanitize_filename(name)}"
    taken = await taken_paths(s, location["id"])
    placed: FileStat | None = None
    async with _opened(s, location, net=net, resolver=resolver) as backend:
        if (await _health(backend)).status != "ok":
            raise ProblemError(409, "location_offline", "The location is offline.")
        while placed is None:
            path = await free_path(wanted, taken, backend)
            try:
                placed = await backend.write(path, _one_chunk(body), None)
            except PreconditionFailed:
                continue  # made since the stat: take the next name
            except (StorageError, AdapterError) as exc:
                raise _storage_problem(exc) from exc
    document_id, version, version_id = await create_file_document(
        s, project_id, location["id"], placed.path, body, source=UPLOAD_SOURCE
    )
    await record_file(
        s,
        location["id"],
        placed,
        content_hash=hashlib.sha256(body).hexdigest(),
        origin="tumnis",
        document_id=document_id,
        synced_version=version,
        last_op="place_upload",
    )
    return UploadPlaced(
        document_id=document_id, version_id=version_id, location_id=location["id"], path=placed.path
    )


class DocumentVersionOut(BaseModel):
    id: UUID
    document_id: UUID
    version_no: int
    content_hash: str  # sha256 hex
    size: int
    body_md: str | None


def _version_out(row: Row) -> DocumentVersionOut:
    return DocumentVersionOut(
        id=row["id"],
        document_id=row["document_id"],
        version_no=row["version_no"],
        content_hash=bytes(row["content_hash"]).hex(),
        size=row["size"],
        body_md=row["body_md"],
    )


async def _version_rows(s: AsyncSession, document_id: UUID) -> Sequence[RowMapping]:
    return (
        (
            await s.execute(
                select(_versions)
                .where(_versions.c.document_id == document_id, _versions.c.deleted_at.is_(None))
                .order_by(_versions.c.version_no)
            )
        )
        .mappings()
        .all()
    )


async def list_document_versions(s: AsyncSession, document_id: UUID) -> list[DocumentVersionOut]:
    """Every kept version of the Document, oldest first (FR-15.6)."""
    return [_version_out(row) for row in await _version_rows(s, document_id)]


async def released_versions(s: AsyncSession, document_id: UUID) -> list[DocumentVersionOut]:
    """A live document's versions as served (`GET .../versions`, P1-17): like `_dto`, a
    file's text only once released, so a version's body is shown when that version and
    the document are both `ready` (a note's always are its own); 404 for a document that
    is trashed or missing."""
    doc = await _live_row(s, document_id)
    note = is_note(doc)
    out: list[DocumentVersionOut] = []
    for row in await _version_rows(s, document_id):
        version = _version_out(row)
        if not note and (row["status"] != "ready" or doc["status"] != "ready"):
            version = version.model_copy(update={"body_md": None})
        out.append(version)
    return out


async def get_document_version(s: AsyncSession, version_id: UUID) -> DocumentVersionOut:
    row = (
        (
            await s.execute(
                select(_versions).where(
                    _versions.c.id == version_id, _versions.c.deleted_at.is_(None)
                )
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound("document_versions", version_id)
    return _version_out(row)


class BackupSource(BaseModel):
    """One folder the nightly backup copies (REL-1): `source` is what `rclone copy` reads
    (a server path, or `tumnis-<location id>:<bucket>/<prefix>/<folder>` for S3, a remote
    the backup host configures), `dest` the project's own place under the backup remote."""

    project_id: UUID
    location_id: UUID
    source: str
    dest: str
    mode: Literal["tumnis_made", "existing"]


async def backup_sources(s: AsyncSession) -> list[BackupSource]:
    """Every live Tumnis-made project folder of the workspace, and every existing folder
    whose project opted in (`backup_opt_in`)."""
    rows = (
        await s.execute(
            select(
                _folders.c.workspace_id,
                _folders.c.project_id,
                _folders.c.location_id,
                _folders.c.root_path,
                _folders.c.mode,
                _locations.c.kind,
                _locations.c.root,
            )
            .join(_locations, _locations.c.id == _folders.c.location_id)
            .where(
                _folders.c.deleted_at.is_(None),
                _locations.c.deleted_at.is_(None),
                (_folders.c.mode == "tumnis_made") | _folders.c.backup_opt_in,
            )
            .order_by(_folders.c.project_id)
        )
    ).all()
    found = []
    for row in rows:
        if row.kind == "s3":
            bucket, prefix = _s3_root(row.root)
            base = f"{bucket}/{prefix}" if prefix else bucket
            source = f"tumnis-{row.location_id}:{base}/{row.root_path}"
        else:
            source = f"{row.root}/{row.root_path}"
        found.append(
            BackupSource(
                project_id=row.project_id,
                location_id=row.location_id,
                source=source,
                dest=f"{row.workspace_id}/{row.project_id}",
                mode=row.mode,
            )
        )
    return found


async def backup_manifest(workspace_ids: Sequence[UUID]) -> list[BackupSource]:
    """`backup_sources` of each workspace, read as the system actor (the CLI's
    `tumnis knowledge backup-sources`)."""
    found: list[BackupSource] = []
    for workspace_id in workspace_ids:
        async with tenant_session(WorkspaceContext(workspace_id, SYSTEM_ACTOR)) as s:
            found += await backup_sources(s)
    return found


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


async def get_document(s: AsyncSession, document_id: UUID) -> DocumentDTO:
    """A live document (404 in the trash): what the upload flow polls and the rail reads
    (P1-17: the one DocumentDTO, a superset of `DocumentStatusOut`)."""
    return _dto(await _live_row(s, document_id))


PDF_MIME: Final = "application/pdf"


@dataclasses.dataclass(frozen=True)
class DownloadInfo:
    """What the file route needs to answer: the name to offer, the path on its location and
    the size the location reports."""

    name: str
    project_id: UUID | None
    location_id: UUID
    path: str
    size: int
    # The served version's bytes sniffed as a PDF (P1-16's libmagic type, never the name):
    # served inline so a citation opens the browser's viewer at `#page=n` (Scott decision
    # 47, P2-17).
    pdf: bool = False


async def _newer_version_unreleased(
    s: AsyncSession, document_id: UUID, current_version_id: UUID | None
) -> bool:
    """Whether a version after the current one is not `ready` (scanning, quarantined,
    failed). A folder replacement is written in place before it is scanned, so the file at
    the document's path may already be that version's bytes."""
    newer = _versions.c.status != "ready"
    if current_version_id is not None:
        current_no = (
            select(_versions.c.version_no)
            .where(_versions.c.id == current_version_id)
            .scalar_subquery()
        )
        newer = and_(newer, _versions.c.version_no > current_no)
    found = await s.scalar(
        select(_versions.c.id).where(_versions.c.document_id == document_id, newer).limit(1)
    )
    return found is not None


async def download_info(
    ctx: WorkspaceContext, document_id: UUID, *, version_no: int | None, net: NetPolicy
) -> DownloadInfo:
    """The document's file if it may be served: 404 for a document or version that does
    not exist, 409 `not_available` unless it is `ready` (still scanning, quarantined,
    failed), unless the pipeline has released a version of it (a current version: only
    its last step sets one, so a row `ready` by the column default is never served, #99),
    unless a requested version is the current one (the folder keeps one file per
    document) and unless no later version is still unreleased."""
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
        status, source_name, mime = doc["status"], None, None
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
            status, source_name, mime = ver["status"], ver["source_name"], ver["mime"]
            current = doc["current_version_id"]
            if current is not None and ver["id"] != current:
                status = "replaced"  # the folder keeps the current version's bytes only
        if (
            status != "ready"
            or doc["current_version_id"] is None
            or doc["path"] is None
            or doc["storage_location_id"] is None
        ):
            raise ProblemError(409, "not_available", "The file is not available yet")
        if await _newer_version_unreleased(s, document_id, doc["current_version_id"]):
            raise ProblemError(409, "not_available", "The file is not available yet")
        if version_no is None:
            current_ver = (
                await s.execute(
                    select(_versions.c.source_name, _versions.c.mime).where(
                        _versions.c.id == doc["current_version_id"]
                    )
                )
            ).first()
            if current_ver is not None:
                source_name, mime = current_ver.source_name, current_ver.mime
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
        pdf=mime == PDF_MIME,
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


# --- Knowledge items, search and passages (P1-17, FR-15.1, FR-15.3 to FR-15.6) -----------
#
# Text entries, links and uploads are Documents; every write of a text entry keeps a version
# (`document_versions`), is indexed into `chunks` by its Markdown headings
# (rules.markdown_sections) and becomes the document's current version, so search reads one
# join for text and files alike. In a project with a folder the note is also written to
# `notes/` through P1-15's path (`save_note`). Trust follows the origin (rules.default_trust)
# and only a person changes it (`mark_trusted`, audited). Trash is `deleted_at`; restore
# clears it; `purge_trash` deletes for good.

KNOWLEDGE_QUOTA_KEY: Final = "knowledge.quota_bytes"
DEFAULT_QUOTA_BYTES: Final = 10 * 1024**3  # plan default: 10 GiB per workspace
LINK_SOURCE: Final = "link"
MAX_TAGS, MAX_TAG_CHARS = 20, 50  # plan defaults
SEARCH_LIMIT_MAX: Final = 50
PASSAGE_SEARCH_LIMIT: Final = 20  # ranked hits passages_for chooses from
HEADLINE_OPTIONS: Final = "MaxFragments=1, MaxWords=30, MinWords=10, StartSel=**, StopSel=**"
TS_CONFIG: Final = "english"  # the configuration chunk_tsv indexes with (knowledge_0006)
_chunks: Table = Chunk.__table__  # type: ignore[assignment]
_artifacts: Table = ExtractionArtifact.__table__  # type: ignore[assignment]


class QuotaSetting(BaseModel):
    quota_bytes: int = Field(default=DEFAULT_QUOTA_BYTES, ge=0)


class Quota(BaseModel):
    """Bytes the workspace's knowledge uses (current file versions plus text entries,
    trash included until it is purged) against its quota; `count` and `project_bytes` are
    the asked scope's (a project, or the workspace knowledge base)."""

    used_bytes: int
    quota_bytes: int
    project_id: UUID | None
    count: int
    project_bytes: int


class KnowledgeHit(BaseModel):
    """One chunk found by `search_knowledge`, citing its document, heading path and page."""

    chunk_id: UUID
    document_id: UUID
    document_title: str
    project_id: UUID | None
    heading_path: list[str]
    page: int | None
    page_to: int | None
    snippet: str
    text: str
    rank: float
    trust: Literal["trusted", "untrusted"]
    tainted: bool


def _dto(row: Row) -> DocumentDTO:
    """The row as served: a file's text stays hidden until it is released (`ready`);
    an app text entry's body is always its own (#99)."""
    data = dict(row)
    if data.get("status") != "ready" and not is_note(data):
        data["body_md"] = None
    return DocumentDTO.model_validate(data)


async def _live_row(s: AsyncSession, document_id: UUID) -> RowMapping:
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
    return row


async def _index_text(s: AsyncSession, document_id: UUID, version_id: UUID, body: str) -> None:
    """The version's chunks: the body's Markdown sections."""
    rows = [
        ChunkRow(
            ordinal=n,
            text=text_part,
            context_text="\n".join([*path, text_part]),
            heading_path=path,
            page_from=None,
            page_to=None,
        )
        for n, (path, text_part) in enumerate(markdown_sections(body))
    ]
    await store.replace_chunks(s, document_id, version_id, rows)


async def _has_folder(s: AsyncSession, project_id: UUID | None) -> bool:
    if project_id is None:
        return False
    found = await s.scalar(
        select(_folders.c.id).where(
            _folders.c.project_id == project_id, _folders.c.deleted_at.is_(None)
        )
    )
    return found is not None


async def _write_text(  # the row, its new body, and where the note goes
    s: AsyncSession,
    row: Row,
    body: str,
    *,
    expected_version: int,
    net: NetPolicy | None,
    event: type[DocumentAddedV1] | type[DocumentChangedV1],
    by_agent: bool = False,
    tainted: bool = False,
    subfolder: str = NOTES,
) -> DocumentDTO:
    """Version, index and file one text write (a new file goes to the project folder's
    `subfolder`): the row updated (versioned, so its row lock
    orders concurrent writers and a stale one gets 409 before any version is added), a new
    version holding `body` as the current one, its chunks, its note file when the project
    has a folder, and `document.added` or `document.changed`. A write `by_agent` leaves
    the entry untrusted (FR-15.5) until a person marks it trusted again; a `tainted` write
    (its caller is: `agent_surface.caller_tainted`) taints it, and nothing lowers that."""
    data = body.encode()
    values: dict[str, Any] = {
        "body_md": body,
        "content_hash": hashlib.sha256(data).digest(),
        "status": "ready",
    }
    if by_agent:
        values["trust"] = "untrusted"
    if tainted:
        values["tainted"] = True
    try:
        await update_versioned(s, _documents, row["id"], expected_version, values)
    except StaleVersion as exc:
        current = _dto(exc.current).model_dump(mode="json")
        raise StaleVersion(current=current) from None
    version_id = await add_version(s, row["id"], data, body, status="ready")
    saved = (
        (
            await s.execute(
                update(_documents)
                .where(_documents.c.id == row["id"])
                .values(current_version_id=version_id)
                .returning(*_documents.c)
            )
        )
        .mappings()
        .one()
    )
    await _index_text(s, row["id"], version_id, body)
    if net is not None and await _has_folder(s, saved["project_id"]):
        await save_note(s, row["id"], net=net, version_id=version_id, subfolder=subfolder)
    version_no = await s.scalar(select(_versions.c.version_no).where(_versions.c.id == version_id))
    await emit(
        s,
        event(
            document_id=row["id"],
            version_id=version_id,
            version_no=version_no or 1,
            project_id=saved["project_id"],
            title=saved["title"],
            trust=saved["trust"],
            size=len(data),
        ),
        occurred_at=SystemClock().now(),
    )
    announce(s, saved["project_id"], row["id"])
    return _dto(saved)


async def _check_project(s: AsyncSession, project_id: UUID | None) -> None:
    if project_id is not None and not await projects.project_exists(s, project_id):
        raise NotFound("projects", project_id)


async def create_text_entry(  # the entry's fields, plus who wrote it
    s: AsyncSession,
    project_id: UUID | None,
    title: str,
    markdown: str,
    *,
    origin: Literal["user_text", "agent"] = "user_text",
    net: NetPolicy | None = None,
    tainted: bool = False,
    tags: Sequence[str] = (),
) -> DocumentDTO:
    """A text entry in the project's knowledge base (None: the workspace knowledge base,
    which every project sees; FR-15.1), with trust by origin (FR-15.5): a person's text is
    trusted, an agent's untrusted and labeled `agent` until a person marks it trusted. The
    first write is version 1; with `net`, a project with a folder gets the note file too.
    A `tainted` caller (a key with no run, a tainted run's token) writes a tainted entry,
    like `add_document`."""
    await _check_project(s, project_id)
    trust, by_origin = default_trust(origin)
    tainted = tainted or by_origin
    row = (
        (
            await s.execute(
                insert(_documents)
                .values(
                    project_id=project_id,
                    title=title,
                    kind="text",
                    role=None,
                    body_md=markdown,
                    trust=trust,
                    tainted=tainted,
                    pinned=False,
                    tags=_clean_tags(tags),
                    content_hash=hashlib.sha256(markdown.encode()).digest(),
                    source=AGENT_SOURCE if origin == "agent" else TEXT_SOURCE,
                    status="ready",
                )
                .returning(*_documents.c)
            )
        )
        .mappings()
        .one()
    )
    return await _write_text(
        s, row, markdown, expected_version=row["version"], net=net, event=DocumentAddedV1
    )


async def update_text_entry(
    s: AsyncSession,
    document_id: UUID,
    markdown: str,
    *,
    expected_version: int,
    net: NetPolicy | None = None,
    origin: Literal["user_text", "agent"] = "user_text",
    tainted: bool = False,
) -> DocumentDTO:
    """Replace a text entry's Markdown body: a new version, re-indexed (FR-15.6). Synced
    files and uploads are not edited here: anything but a text entry (`is_note`) is 409
    `not_text`; a stale `expected_version` is 409 `stale_version` with the current entry.
    An agent's edit leaves the entry untrusted (FR-15.5); a `tainted` caller's taints it."""
    row = await _live_row(s, document_id)
    if not is_note(row) and row["source"] != AGENT_SOURCE:
        raise ProblemError(409, "not_text", "Only text entries can be edited here")
    return await _write_text(
        s,
        row,
        markdown,
        expected_version=expected_version,
        net=net,
        event=DocumentChangedV1,
        by_agent=origin == "agent",
        tainted=tainted,
    )


async def add_link(
    s: AsyncSession, project_id: UUID | None, url: str, title: str | None
) -> DocumentDTO:
    """A link kept as a knowledge item: trusted, never fetched (FR-15.5); the URL is its
    `provider_url` and it has no body, version or chunks."""
    await _check_project(s, project_id)
    trust, tainted = default_trust("link")
    row = (
        (
            await s.execute(
                insert(_documents)
                .values(
                    project_id=project_id,
                    title=title or url,
                    kind="link",
                    trust=trust,
                    tainted=tainted,
                    provider_url=url,
                    content_hash=hashlib.sha256(url.encode()).digest(),
                    source=LINK_SOURCE,
                    status="ready",
                )
                .returning(*_documents.c)
            )
        )
        .mappings()
        .one()
    )
    announce(s, project_id, row["id"])
    return _dto(row)


async def mark_trusted(
    s: AsyncSession,
    document_id: UUID,
    trusted: bool,
    *,
    now: datetime,
    expected_version: int | None = None,
) -> DocumentDTO:
    """A person marks the document trusted or untrusted (FR-15.5), audited as
    `document.trust_changed`. Only a person (a session) may: anything else is 403
    `human_only`. The taint stays as it was: it records where the text came from. With
    `expected_version` (the version the person reviewed), a document that has moved on
    since is 409 `stale_version` with its current state, and nothing changes."""
    ctx = tenancy.current()
    if ctx is None or not str(ctx.actor).startswith("user:"):
        raise ProblemError(403, "human_only", "Only a person can change a document's trust")
    row = await _live_row(s, document_id)
    if expected_version is not None and row["version"] != expected_version:
        raise StaleVersion(current=_dto(row).model_dump(mode="json"))
    trust = "trusted" if trusted else "untrusted"
    if row["trust"] != trust:
        stmt = update(_documents).where(_documents.c.id == document_id)
        if expected_version is not None:
            # An edit committed after the read above still makes the request stale.
            stmt = stmt.where(_documents.c.version == expected_version)
        changed = (
            (await s.execute(stmt.values(trust=trust).returning(*_documents.c))).mappings().first()
        )
        if changed is None:
            current = _dto(await _live_row(s, document_id)).model_dump(mode="json")
            raise StaleVersion(current=current)
        row = changed
    await audit.record(
        s,
        "document.trust_changed",
        target=("document", document_id),
        details={"trust": trust},
        occurred_at=now,
    )
    announce(s, row["project_id"], row["id"])
    return _dto(row)


def _clean_tags(tags: Sequence[str]) -> list[str]:
    """Trimmed, distinct (ignoring case, first spelling kept), none empty; 422 `invalid_tags`
    past MAX_TAGS or MAX_TAG_CHARS."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        tag = raw.strip()
        if not tag or tag.casefold() in seen:
            continue
        if len(tag) > MAX_TAG_CHARS:
            raise ProblemError(422, "invalid_tags", f"A tag is at most {MAX_TAG_CHARS} characters")
        seen.add(tag.casefold())
        out.append(tag)
    if len(out) > MAX_TAGS:
        raise ProblemError(422, "invalid_tags", f"At most {MAX_TAGS} tags")
    return out


async def _set_fields(
    s: AsyncSession, document_id: UUID, values: Mapping[str, Any], expected_version: int
) -> DocumentDTO:
    try:
        row = await update_versioned(s, _documents, document_id, expected_version, values)
    except StaleVersion as exc:
        raise StaleVersion(current=_dto(exc.current).model_dump(mode="json")) from None
    announce(s, row["project_id"], row["id"])
    return _dto(row)


async def set_tags(
    s: AsyncSession, document_id: UUID, tags: list[str], *, expected_version: int
) -> DocumentDTO:
    return await _set_fields(s, document_id, {"tags": _clean_tags(tags)}, expected_version)


async def set_pinned(
    s: AsyncSession, document_id: UUID, pinned: bool, *, expected_version: int
) -> DocumentDTO:
    return await _set_fields(s, document_id, {"pinned": pinned}, expected_version)


async def edit_document(  # the patchable fields, each optional
    s: AsyncSession,
    document_id: UUID,
    *,
    expected_version: int,
    body_md: str | None = None,
    title: str | None = None,
    tags: list[str] | None = None,
    pinned: bool | None = None,
    net: NetPolicy | None = None,
    origin: Literal["user_text", "agent"] = "user_text",
    tainted: bool = False,
) -> DocumentDTO:
    """One versioned edit of a document (the rail's PATCH): its metadata (title, tags,
    pin) first, then a text entry's body as a new version (`origin` and `tainted` as in
    `update_text_entry`). The first change takes `expected_version`; a body change after
    it takes the version that change left."""
    values: dict[str, Any] = {}
    if title is not None:
        values["title"] = title
    if tags is not None:
        values["tags"] = _clean_tags(tags)
    if pinned is not None:
        values["pinned"] = pinned
    version = expected_version
    dto: DocumentDTO | None = None
    if body_md is not None:
        row = await _live_row(s, document_id)
        if not is_note(row) and row["source"] != AGENT_SOURCE:
            raise ProblemError(409, "not_text", "Only text entries can be edited here")
    if values:
        dto = await _set_fields(s, document_id, values, version)
        version = dto.version
    if body_md is not None:
        dto = await update_text_entry(
            s,
            document_id,
            body_md,
            expected_version=version,
            net=net,
            origin=origin,
            tainted=tainted,
        )
    if dto is None:
        dto = _dto(await _live_row(s, document_id))
        if dto.version != expected_version:
            raise StaleVersion(current=dto.model_dump(mode="json"))
    return dto


async def trash(s: AsyncSession, document_id: UUID) -> None:
    """To the trash (FR-15.6): hidden from lists, reads and search until restored."""
    await trash_document(s, document_id)


async def restore(s: AsyncSession, document_id: UUID) -> DocumentDTO:
    """Back from the trash; 404 for a document that is not in it."""
    row = (
        (
            await s.execute(
                update(_documents)
                .where(_documents.c.id == document_id, _documents.c.deleted_at.is_not(None))
                .values(deleted_at=None)
                .returning(*_documents.c)
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound("documents", document_id)
    announce(s, row["project_id"], row["id"])
    return _dto(row)


async def purge_trash(s: AsyncSession, cutoff: datetime, *, limit: int) -> int:
    """Hard-deletes up to `limit` documents trashed before `cutoff`, with their versions,
    chunks, extraction artifacts and queued note writes; a folder file record stays (its
    file is the folder sync's) without its document. Returns how many went. The picked
    documents are locked first, skipping any a restore holds, so a restore either waits
    for the purge (and finds nothing) or keeps its document."""
    ids: list[UUID] = list(
        await s.scalars(
            select(_documents.c.id)
            .where(_documents.c.deleted_at < cutoff)
            .order_by(_documents.c.deleted_at, _documents.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    if not ids:
        return 0
    versions = select(_versions.c.id).where(_versions.c.document_id.in_(ids))
    await s.execute(delete(_pending).where(_pending.c.document_version_id.in_(versions)))
    await s.execute(delete(_chunks).where(_chunks.c.document_id.in_(ids)))
    await s.execute(delete(_artifacts).where(_artifacts.c.version_id.in_(versions)))
    await s.execute(update(_files).where(_files.c.document_id.in_(ids)).values(document_id=None))
    await s.execute(
        update(_documents).where(_documents.c.id.in_(ids)).values(current_version_id=None)
    )
    await s.execute(delete(_versions).where(_versions.c.document_id.in_(ids)))
    result = await s.execute(
        delete(_documents).where(_documents.c.id.in_(ids)).returning(_documents.c.id)
    )
    return len(result.all())


def _scope(
    project_id: UUID | None, project_ids: frozenset[UUID] | None
) -> ColumnElement[bool] | None:
    """Which documents a reader sees: a project's items and the workspace knowledge base;
    without a project, everything (a project-limited key: its projects and the knowledge
    base)."""
    shared = _documents.c.project_id.is_(None)
    if project_id is not None:
        return or_(_documents.c.project_id == project_id, shared)
    if project_ids is not None:
        return or_(_documents.c.project_id.in_(project_ids), shared)
    return None


async def list_documents(
    s: AsyncSession,
    project_id: UUID | None,
    *,
    cursor: str | None,
    limit: int,
    project_ids: frozenset[UUID] | None = None,
) -> Page[DocumentDTO]:
    """A project's knowledge items (None: the workspace knowledge base's), live ones only,
    in the order they were added; a project-limited caller sees only its projects."""
    if project_id is None:
        where: ColumnElement[bool] = _documents.c.project_id.is_(None)
    elif project_ids is not None and project_id not in project_ids:
        raise NotFound("projects", project_id)
    else:
        where = _documents.c.project_id == project_id
    stmt = select(_documents).where(where, _documents.c.deleted_at.is_(None))
    page = await paginate(
        s, stmt, keys=[], id_col=_documents.c.id, cursor=cursor, limit=limit, model=DocumentDTO
    )
    # Served like a single read (`_dto`): a file's text only once it is released.
    return page.model_copy(update={"items": [_dto(doc.model_dump()) for doc in page.items]})


def _search_stmt(
    q: str,
    project_id: UUID | None,
    project_ids: frozenset[UUID] | None,
    *,
    agent_written: bool = True,
    chunk_ids: Sequence[UUID] | None = None,
) -> Select[Any]:
    """The ranked hits of `q` in the reader's scope (`_scope`), best first, unlimited;
    without the documents agents added (`source` "agent") when `agent_written` is False.
    With `chunk_ids` (hybrid search's vector hits, P3-10): those chunks, under the same
    filters, whether or not they match `q`."""
    query = func.websearch_to_tsquery(TS_CONFIG, q)
    rank = func.ts_rank_cd(_chunks.c.tsv, query).label("rank")
    stmt = (
        select(
            _chunks.c.id.label("chunk_id"),
            _chunks.c.document_id,
            _documents.c.title.label("document_title"),
            _documents.c.project_id,
            _chunks.c.heading_path,
            _chunks.c.page_from.label("page"),
            _chunks.c.page_to,
            func.ts_headline(TS_CONFIG, _chunks.c.text, query, HEADLINE_OPTIONS).label("snippet"),
            _chunks.c.text,
            rank,
            _documents.c.trust,
            _documents.c.tainted,
        )
        .join(
            _documents,
            and_(
                _documents.c.id == _chunks.c.document_id,
                _documents.c.current_version_id == _chunks.c.document_version_id,
            ),
        )
        .where(
            (
                _chunks.c.tsv.bool_op("@@")(query)
                if chunk_ids is None
                else _chunks.c.id.in_(chunk_ids)
            ),
            _chunks.c.deleted_at.is_(None),
            _documents.c.deleted_at.is_(None),
            _documents.c.status == "ready",
        )
        .order_by(
            rank.desc(),
            _documents.c.pinned.desc(),
            _documents.c.updated_at.desc(),
            _chunks.c.ordinal,
            _chunks.c.id,
        )
    )
    if not agent_written:
        stmt = stmt.where(_documents.c.source.is_distinct_from(AGENT_SOURCE))
    scope = _scope(project_id, project_ids)
    return stmt if scope is None else stmt.where(scope)


async def search_knowledge(  # R-36's parameters, plus the caller's limit
    s: AsyncSession,
    q: str,
    *,
    project_id: UUID | None,
    limit: int = 10,
    mode: Literal["fts", "hybrid"] = "fts",
    project_ids: frozenset[UUID] | None = None,
    agent_written: bool = True,
) -> list[KnowledgeHit]:
    """Full-text search (R-36) over the current versions' chunks of live, ready documents:
    a project's items and the workspace knowledge base (FR-15.1), ranked by cover density,
    then pinned and recently changed documents first. Row-level security scopes it to the
    workspace first. `mode="hybrid"` (P3-10, FR-15.3) fuses it with the vector search
    (`knowledge.search`), and answers the full-text hits when no query vector exists."""
    if mode not in {"fts", "hybrid"}:
        raise ProblemError(422, "invalid_mode", "Search mode is 'fts' or 'hybrid'")
    if not q.strip():
        return []
    size = max(1, min(limit, SEARCH_LIMIT_MAX))

    async def hits(limit: int, chunk_ids: Sequence[UUID] | None = None) -> list[KnowledgeHit]:
        return await _fulltext_hits(
            s,
            q,
            project_id,
            project_ids,
            agent_written=agent_written,
            limit=limit,
            chunk_ids=chunk_ids,
        )

    if mode == "fts":
        return await hits(limit=size)
    # Hybrid: never through `search_knowledge` itself, so a search is one call of it.
    fulltext = await hits(limit=max(CANDIDATES, size))
    fused = await search.fuse(
        s, q, [h.chunk_id for h in fulltext], project_id=project_id, project_ids=project_ids
    )
    if fused is None:  # no query vector: exactly the full-text answer
        return fulltext[:size]
    found = {h.chunk_id: h for h in fulltext}
    vector_only = [f.chunk_id for f in fused if f.chunk_id not in found]
    if vector_only:  # read back under the full-text path's filters (scope, live, ready)
        found |= {h.chunk_id: h for h in await hits(limit=len(vector_only), chunk_ids=vector_only)}
    fused_hits = [
        found[f.chunk_id].model_copy(update={"rank": f.score}) for f in fused if f.chunk_id in found
    ]
    return fused_hits[:size]


async def _fulltext_hits(  # _search_stmt's arguments, and how many
    s: AsyncSession,
    q: str,
    project_id: UUID | None,
    project_ids: frozenset[UUID] | None,
    *,
    agent_written: bool,
    limit: int,
    chunk_ids: Sequence[UUID] | None = None,
) -> list[KnowledgeHit]:
    """The first `limit` full-text hits (with `chunk_ids`: those chunks, read through the
    same filters, whether or not they match `q`)."""
    stmt = _search_stmt(
        q, project_id, project_ids, agent_written=agent_written, chunk_ids=chunk_ids
    ).limit(limit)
    rows = (await s.execute(stmt)).mappings().all()
    return [KnowledgeHit.model_validate(dict(row)) for row in rows]


SEARCH_DEPTH_MAX: Final = 500  # how far down the ranking a search pages (plan: top hits)


async def search_page(
    s: AsyncSession,
    q: str,
    *,
    project_id: UUID | None,
    limit: int = 10,
    cursor: str | None = None,
    project_ids: frozenset[UUID] | None = None,
) -> Page[KnowledgeHit]:
    """`search_knowledge` a page at a time (the `search_knowledge` tool and its twin,
    P2-17). A rank is no key to continue after, so the cursor holds the position in the
    ranking; the walk ends at `SEARCH_DEPTH_MAX` hits. 400 `invalid_cursor` for a cursor
    that is not one."""
    offset = 0
    if cursor is not None:
        offset = Cursor.decode(cursor, [int]).keys[0]
        if not 0 < offset < SEARCH_DEPTH_MAX:
            raise invalid_cursor()
    size = max(1, min(limit, SEARCH_LIMIT_MAX, SEARCH_DEPTH_MAX - offset))
    if not q.strip():
        return Page[KnowledgeHit](items=[], next_cursor=None)
    stmt = _search_stmt(q, project_id, project_ids).offset(offset).limit(size + 1)
    rows = (await s.execute(stmt)).mappings().all()
    hits = [KnowledgeHit.model_validate(dict(row)) for row in rows[:size]]
    more = len(rows) > size and offset + size < SEARCH_DEPTH_MAX
    next_cursor = Cursor((offset + size,), hits[-1].chunk_id).encode() if more else None
    return Page[KnowledgeHit](items=hits, next_cursor=next_cursor)


async def passages_for(
    s: AsyncSession, task_id: UUID, *, cap: int = PASSAGE_CAP_CHARS
) -> list[Passage]:
    """The knowledge part of a task's packet (FR-15.4): the project brief first, then the
    passages the task's title, acceptance criteria and project goal find (rules
    `passage_query`, `select_passages`), each citing its document and page, within `cap`
    characters. 404 for a task the caller cannot see."""
    task = await tasks.get_task(s, task_id)
    context = await projects.project_context(s, task.project_id)
    brief: Passage | None = None
    try:
        doc = await get_brief(task.project_id, session=s)
    except NotFound:
        doc = None
    if doc is not None and doc.body_md:
        brief = Passage(
            chunk_id=None,
            document_id=doc.id,
            title=doc.title,
            heading_path=[],
            page=None,
            text=doc.body_md,
            tainted=doc.tainted,
        )
    criteria = (task.acceptance_criteria or "").splitlines()
    q = passage_query(task.title, criteria, context.goal)
    # What agents added stays out of packets (an agent finds it with `search_knowledge`),
    # so one run's output never feeds the next run's packet on its own (P2-17, T-P2-08-06).
    # Hybrid (P3-10): full text and vectors fused; full text alone without a query vector.
    hits = await search_knowledge(
        s,
        q,
        project_id=task.project_id,
        limit=PASSAGE_SEARCH_LIMIT,
        agent_written=False,
        mode="hybrid",
    )
    ranked = [
        Passage(
            chunk_id=h.chunk_id,
            document_id=h.document_id,
            title=h.document_title,
            heading_path=h.heading_path,
            page=h.page,
            text=h.text,
            tainted=h.tainted,
        )
        for h in hits
    ]
    return select_passages(brief, ranked, cap)


def _used_bytes() -> ColumnElement[int]:
    """A document's bytes: a text entry's body (UTF-8), a file's current version; links
    and files not yet released count nothing."""
    note = and_(_documents.c.kind == "text", _documents.c.source.in_([TEXT_SOURCE, AGENT_SOURCE]))
    size = (
        select(_versions.c.size)
        .where(_versions.c.id == _documents.c.current_version_id)
        .scalar_subquery()
    )
    return case(
        (note, func.octet_length(func.coalesce(_documents.c.body_md, ""))),
        else_=func.coalesce(size, 0),
    )


async def quota(s: AsyncSession, project_id: UUID | None) -> Quota:
    """The workspace's used bytes (trash counts until it is purged) against its quota
    (`knowledge.quota_bytes`, 10 GiB by default), and the scope's item count and bytes.
    404 for a project the caller cannot see."""
    ctx = tenancy.current()
    if ctx is None:
        raise RuntimeError("quota runs in a workspace context")
    await _check_project(s, project_id)
    setting = await settings_store.get_setting(ctx, KNOWLEDGE_QUOTA_KEY, QuotaSetting)
    limit = setting.value.quota_bytes if setting is not None else DEFAULT_QUOTA_BYTES
    used = _used_bytes()
    total = await s.scalar(select(func.coalesce(func.sum(used), 0)))
    in_scope = (
        _documents.c.project_id.is_(None)
        if project_id is None
        else _documents.c.project_id == project_id
    )
    scoped = (
        await s.execute(
            select(func.count(), func.coalesce(func.sum(used), 0)).where(
                in_scope, _documents.c.deleted_at.is_(None)
            )
        )
    ).one()
    return Quota(
        used_bytes=int(total or 0),
        quota_bytes=limit,
        project_id=project_id,
        count=int(scoped[0]),
        project_bytes=int(scoped[1]),
    )


# --- SFTP host keys and existing folders (P3-14): red-phase seams --------------------------


async def use_existing_folder(
    s: AsyncSession,
    project_id: UUID,
    *,
    location_id: UUID,
    path: str,
    net: NetPolicy,
    resolver: Resolver = system_resolver,
) -> ProjectFolderOut:
    """Make a folder the user already keeps the project's folder (mode `existing`,
    FR-15.12): Tumnis makes `Tumnis/` with `uploads/`, `notes/`, `agent-outputs/` and
    `.trash/` inside it and writes nothing else; the next folder sync indexes what it
    finds as outside files. The location must answer (409 `location_offline`); a folder
    that already holds this project's files is not re-pointed (409 `folder_not_empty`,
    moving files is the move job), and a folder that is, holds or sits inside another
    project's folder on the location is refused (409 `folder_taken`)."""
    try:
        root = safe_rel_path(path.strip("/"))
    except PathRejected as exc:
        raise _storage_problem(exc) from exc
    location = await _location_row(s, location_id)
    _require_online(location)
    if not await projects.project_exists(s, project_id):
        raise NotFound("projects", project_id)
    current = (
        (await s.execute(select(_folders).where(_folders.c.project_id == project_id)))
        .mappings()
        .first()
    )
    if current is not None and (current["location_id"], current["root_path"]) != (
        location_id,
        root,
    ):
        prefix = current["root_path"] + "/"
        held = await s.scalar(
            select(func.count())
            .select_from(_files)
            .where(
                _files.c.location_id == current["location_id"],
                _files.c.path.startswith(prefix, autoescape=True),
                _files.c.deleted_at.is_(None),
            )
        )
        if held:
            raise ProblemError(409, "folder_not_empty", "The project folder already holds files.")
    # One claim at a time per location (as `assign_project_folder` names folders), so
    # two setups cannot both pass the overlap check.
    await s.execute(_FOLDER_NAME_LOCK, {"key": f"project-folder-name:{location_id}"})
    others: list[str] = list(
        await s.scalars(
            select(_folders.c.root_path).where(
                _folders.c.location_id == location_id,
                _folders.c.project_id != project_id,
                _folders.c.deleted_at.is_(None),
            )
        )
    )
    for other in others:  # under the lock taken above, held to the upsert
        if other == root or other.startswith(root + "/") or root.startswith(other + "/"):
            raise ProblemError(409, "folder_taken", "Another project uses that folder.")
    async with _opened(s, location, net=net, resolver=resolver) as backend:
        if (await _health(backend)).status != "ok":
            raise ProblemError(409, "location_offline", "The location is offline.")
        try:
            for sub in EXISTING_LAYOUT:
                await backend.ensure_folder(f"{root}/{EXISTING_DIR}/{sub}")
        except (StorageError, AdapterError) as exc:
            raise _storage_problem(exc) from exc
    values = {"location_id": location_id, "root_path": root, "mode": "existing"}
    await s.execute(
        pg_insert(_folders)
        .values(project_id=project_id, **values)
        .on_conflict_do_update(
            index_elements=[_folders.c.workspace_id, _folders.c.project_id],
            set_={**values, "version": _folders.c.version + 1, "deleted_at": None},
        )
    )
    mark_changed(s, "project", project_id)
    return await get_project_folder(s, project_id)


async def rename_document(s: AsyncSession, document_id: UUID, *, title: str) -> DocumentDTO:
    """Rename a document; an outside file keeps its name on disk (the title only)."""
    raise NotImplementedError("P3-14")


async def delete_document(s: AsyncSession, document_id: UUID, *, actor: ActorKind) -> str:
    """Delete a document as `actor`; the outcome `may_delete` gives."""
    raise NotImplementedError("P3-14")
