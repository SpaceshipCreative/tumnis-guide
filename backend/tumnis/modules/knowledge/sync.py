"""The folder sync engine (P1-15, FR-15.12): what `knowledge_folder_sync` runs, one call
per DBOS step (`workflows.py`).

1. `begin`: the location's health (its marker, or HeadBucket on S3); the location's status
   follows it and a healthy location drains its queued note writes first
   (`api.check_location`), so a note saved while it was offline lands before the
   comparison and is not taken for a conflict. Offline: nothing else happens.
2. `plan`: list every project folder on the location (Tumnis's `.tumnis/`, temp files and
   OS droppings left out), load `folder_files` and the linked Documents, hash what changed
   (bounded, four at a time), read `tumnis_id` from new notes, pair renames, then
   `decide_sync_action` per path. The plan is plain JSON, recorded by DBOS, so a resumed
   sync applies exactly what was decided.
3. `apply`: one decision, in one transaction, after checking that the record and the
   Document still hold what the plan saw (anything else is left to the next sync). Every
   write carries the decision's precondition; a write that already landed (a crash
   between the write and the commit) counts as done, so a replay writes nothing twice.
4. `request_extraction` for the versions made from folder bytes, then `finish`
   (`last_sync_at`).

The net policy, the clock and the extraction hook come from `configure` and `use`; the
worker configures the net policy at start (tests call `configure` and `use` themselves).
"""

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import date
from pathlib import PurePosixPath
from typing import Any, Final, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import RowMapping, Table, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.adapters.errors import AdapterError
from tumnis.core.clock import Clock, SystemClock
from tumnis.core.errors import ProblemError
from tumnis.core.net import NetPolicy
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.auth import api as auth
from tumnis.modules.knowledge import api
from tumnis.modules.knowledge.models import Document, FolderFile, ProjectFolder, StorageLocation
from tumnis.modules.knowledge.rules import etag_equal
from tumnis.modules.knowledge.storage import (
    FileStat,
    PreconditionFailed,
    StorageBackend,
    StorageError,
    spool,
)
from tumnis.modules.knowledge.storage import NotFound as FileMissing
from tumnis.modules.knowledge.sync_rules import (
    Action,
    Local,
    Prev,
    Remote,
    SyncDecision,
    decide_sync_action,
    note_body,
    pair_renames,
    read_tumnis_id,
    render_note,
)
from tumnis.modules.tasks import api as tasks

log = logging.getLogger(__name__)

HASH_CONCURRENCY: Final = 4  # plan default
HEAD_BYTES: Final = 4096  # how much of a new note is read for its tumnis_id
IGNORED_NAMES: Final = frozenset({".DS_Store", "Thumbs.db"})  # OS droppings (plan default)
TMP_PREFIX: Final = ".tumnis-tmp-"
OFFICE_LOCK: Final = "~$"

ExtractionHook = Callable[[UUID, UUID, str], Awaitable[None]]

_documents: Table = Document.__table__  # type: ignore[assignment]
_files: Table = FolderFile.__table__  # type: ignore[assignment]
_folders: Table = ProjectFolder.__table__  # type: ignore[assignment]
_locations: Table = StorageLocation.__table__  # type: ignore[assignment]


async def _no_extraction(_workspace_id: UUID, _version_id: UUID, _path: str) -> None:
    """Until P1-16's `extract_document` is wired in, nothing is extracted."""


class _Config:
    net: NetPolicy | None = None
    clock: Clock | None = None
    extraction: ExtractionHook | None = None


def configure(net: NetPolicy | None) -> None:
    """The SSRF policy every location is opened with (the worker's settings); None falls
    back to the deployment's settings."""
    _Config.net = net


def use(
    *, clock: Clock | None, extraction: ExtractionHook | None
) -> tuple[Clock | None, ExtractionHook | None]:
    """Swap the clock and the extraction hook (tests); returns the previous pair."""
    previous = (_Config.clock, _Config.extraction)
    _Config.clock, _Config.extraction = clock, extraction
    return previous


def net() -> NetPolicy:
    if _Config.net is not None:
        return _Config.net
    from pydantic import ValidationError  # noqa: PLC0415

    from tumnis.settings import Settings  # noqa: PLC0415

    try:
        return Settings().net_policy()  # read from the environment
    except ValidationError:  # no deployment settings (in-process tests): the default mode
        return NetPolicy(mode="self-hosted")


def clock() -> Clock:
    return _Config.clock or SystemClock()


def _ctx(workspace_id: str) -> WorkspaceContext:
    return WorkspaceContext(UUID(workspace_id), SYSTEM_ACTOR)


def ignored(rel_in_folder: str) -> bool:
    """Tumnis's own `.tumnis/`, its temp files and OS droppings are never synced."""
    name = PurePosixPath(rel_in_folder).name
    return (
        rel_in_folder.split("/", 1)[0] == api.TUMNIS_DIR
        or name.startswith(TMP_PREFIX)
        or name in IGNORED_NAMES
        or name.startswith(OFFICE_LOCK)
    )


# --- 1. Health and queued writes ----------------------------------------------------------


async def begin(workspace_id: str, location_id: str) -> str:
    """'online' when the location answers (its queued writes drained), else 'offline'."""
    async with tenant_session(_ctx(workspace_id)) as s:
        try:
            checked = await api.check_location(s, UUID(location_id), net=net())
        except ProblemError as exc:  # a refused endpoint: nothing is opened
            log.warning("folder sync of %s refused: %s", location_id, exc.code)
            return "refused"
    return checked.status


# --- 2. The plan ----------------------------------------------------------------------------


async def _sha256(backend: StorageBackend, path: str) -> str:
    return hashlib.sha256(await spool(backend.read(path))).hexdigest()


async def _head(backend: StorageBackend, path: str) -> str:
    got = b""
    async for chunk in backend.read(path):
        got += chunk
        if len(got) >= HEAD_BYTES:
            break
    return got[:HEAD_BYTES].decode("utf-8", errors="ignore")


def _prev(row: Mapping[Any, Any]) -> Prev:
    return Prev(
        path=row["path"],
        size=row["size"],
        mtime=row["mtime"],
        etag=row["etag"],
        content_hash=row["content_hash"],
        origin=row["origin"],
        document_id=row["document_id"],
        synced_version=row["synced_version"],
    )


def local_bytes(doc: Mapping[Any, Any]) -> bytes:
    """What Tumnis would write for the Document: a note under its frontmatter, any other
    Document its text."""
    if doc["kind"] == "text":
        return render_note(doc["id"], doc["body_md"] or "").encode()
    return (doc["body_md"] or "").encode()


def _local(doc: Mapping[Any, Any], record: Mapping[Any, Any] | None) -> Local:
    if doc["kind"] == "text":
        digest = hashlib.sha256(local_bytes(doc)).hexdigest()
    else:
        digest = bytes(doc["content_hash"]).hex()
    return Local(
        document_id=doc["id"],
        version=doc["version"],
        content_hash=digest,
        origin=record["origin"] if record is not None else "tumnis",
        trashed=doc["deleted_at"] is not None,
        delete_confirmed=bool(record["delete_confirmed"]) if record is not None else False,
    )


class _Scan:
    """What the folder shows and what the database holds for one location."""

    def __init__(self) -> None:
        self.files: dict[str, FileStat] = {}
        self.project_of: dict[str, UUID] = {}  # path -> the project whose folder holds it
        self.hashes: dict[str, str] = {}
        self.ids: dict[str, UUID | None] = {}  # new notes' tumnis_id
        self.records: dict[str, RowMapping] = {}
        self.docs: dict[UUID, RowMapping] = {}
        self.folders: list[RowMapping] = []


async def _list(backend: StorageBackend, folder: Mapping[Any, Any], scan: _Scan) -> None:
    root = folder["root_path"]
    cursor: str | None = None
    while True:
        page = await backend.list(root + "/", cursor)
        for stat in page.items:
            if not ignored(stat.path[len(root) + 1 :]):
                scan.files[stat.path] = stat
                scan.project_of[stat.path] = folder["project_id"]
        cursor = page.next_cursor
        if cursor is None:
            return


def _differs(stat: FileStat, record: Mapping[Any, Any] | None) -> bool:
    return record is None or (stat.size, stat.mtime, stat.etag) != (
        record["size"],
        record["mtime"],
        record["etag"],
    )


async def _hash_changed(backend: StorageBackend, scan: _Scan, *, etag_is_hash: bool) -> None:
    """Hash every file whose size, mtime or etag differ from its record (a server path's
    etag is the sha256 already), four at a time; read the head of each new note."""
    limit = asyncio.Semaphore(HASH_CONCURRENCY)

    async def one(path: str, stat: FileStat) -> None:
        async with limit:
            record = scan.records.get(path)
            if _differs(stat, record):
                scan.hashes[path] = stat.etag if etag_is_hash else await _sha256(backend, path)
            if record is None and path.lower().endswith(".md"):
                scan.ids[path] = read_tumnis_id(await _head(backend, path))

    await asyncio.gather(*(one(p, st) for p, st in scan.files.items()))


async def _load(s: AsyncSession, location_id: UUID, scan: _Scan) -> None:
    scan.folders = list(
        (
            await s.execute(
                select(_folders).where(
                    _folders.c.location_id == location_id, _folders.c.deleted_at.is_(None)
                )
            )
        )
        .mappings()
        .all()
    )
    records = (
        (
            await s.execute(
                select(_files).where(
                    _files.c.location_id == location_id, _files.c.deleted_at.is_(None)
                )
            )
        )
        .mappings()
        .all()
    )
    scan.records = {row["path"]: row for row in records}
    linked = {row["document_id"] for row in records if row["document_id"] is not None}
    projects = [f["project_id"] for f in scan.folders]
    candidates = (_documents.c.kind == "text") & _documents.c.deleted_at.is_(None)
    docs = (
        (
            await s.execute(
                select(_documents).where(
                    _documents.c.id.in_(linked)
                    | (_documents.c.project_id.in_(projects) & candidates)
                )
            )
        )
        .mappings()
        .all()
    )
    scan.docs = {row["id"]: row for row in docs}


def _remote(stat: FileStat, digest: str | None) -> Remote:
    return Remote(size=stat.size, mtime=stat.mtime, etag=stat.etag, content_hash=digest)


def _item(  # one plan entry, spelled out
    path: str,
    decision: SyncDecision,
    *,
    project_id: UUID | None,
    prev: Prev | None,
    remote: Remote | None,
    local: Local | None,
    rename_from: str | None = None,
) -> dict[str, Any]:
    return {
        "path": path,
        "rename_from": rename_from,
        "project_id": str(project_id) if project_id is not None else None,
        "decision": decision.model_dump(mode="json"),
        "prev": prev.model_dump(mode="json") if prev is not None else None,
        "remote": remote.model_dump(mode="json") if remote is not None else None,
        "local": local.model_dump(mode="json") if local is not None else None,
    }


_Entry = tuple[str, Prev | None, str | None, Local | None, str | None]
# (path, prev, listed path, local, renamed from)


async def plan(workspace_id: str, location_id: str) -> list[dict[str, Any]]:
    """Every decision for the location that does something, in path order."""
    ctx, loc = _ctx(workspace_id), UUID(location_id)
    scan = _Scan()
    async with tenant_session(ctx) as s:
        kind = await s.scalar(select(_locations.c.kind).where(_locations.c.id == loc))
        await _load(s, loc, scan)
        tz = await auth.workspace_timezone(s, ctx.workspace_id)
        today = clock().now().astimezone(ZoneInfo(tz.timezone)).date()
        async with api.open_backend(s, loc, net=net()) as backend:
            for folder in scan.folders:
                if folder["mode"] == "tumnis_made":
                    await api.make_layout(backend, folder["root_path"])
                await _list(backend, folder, scan)
            await _hash_changed(backend, scan, etag_is_hash=kind == "server_path")
        taken = await api.taken_paths(s, loc)
        taken |= {p.casefold() for p in scan.files}
        roots = {f["project_id"]: f["root_path"] for f in scan.folders}
        entries = _listed_entries(scan, roots)
        entries += await _unwritten_entries(s, loc, scan, roots, entries=entries, taken=taken)
    siblings = set(scan.files) | set(scan.records) | {e[0] for e in entries}
    return _decide(scan, roots, entries, siblings, today)


def _listed_entries(scan: _Scan, roots: Mapping[UUID, str]) -> list[_Entry]:
    """One entry per listed file (renames paired) and per record whose file is gone."""
    prevs = {path: _prev(row) for path, row in scan.records.items()}
    missing = [prev for path, prev in prevs.items() if path not in scan.files]
    new = [
        (path, _remote(stat, scan.hashes.get(path)), scan.ids.get(path))
        for path, stat in scan.files.items()
        if path not in prevs
    ]
    pairs = [
        (old, moved)
        for old, moved in pair_renames(missing, new)
        if _same_folder(old, moved, roots.values())
    ]
    renamed = {moved: old for old, moved in pairs}
    gone = {old for old, _moved in pairs}
    linked = {r["document_id"] for r in scan.records.values()}
    entries: list[_Entry] = []
    for path in sorted(scan.files):
        old = renamed.get(path)
        record = scan.records.get(old or path)
        prev = prevs[old].model_copy(update={"path": path}) if old else prevs.get(path)
        doc_id = record["document_id"] if record is not None else None
        if record is None:
            found = scan.ids.get(path)
            doc = scan.docs.get(found) if found is not None else None
            if (
                doc is not None
                and doc["kind"] == "text"
                and doc["id"] not in linked
                and doc["project_id"] == scan.project_of[path]
            ):
                doc_id = doc["id"]  # a note written before its record (a crash)
        entries.append((path, prev, path, _local_of(scan, doc_id, record), old))
    for path, prev in sorted(prevs.items()):
        if path in scan.files or path in gone:
            continue
        record = scan.records[path]
        entries.append((path, prev, None, _local_of(scan, record["document_id"], record), None))
    return entries


async def _unwritten_entries(  # the notes no entry places yet
    s: AsyncSession,
    loc: UUID,
    scan: _Scan,
    roots: Mapping[UUID, str],
    *,
    entries: list[_Entry],
    taken: set[str],
) -> list[_Entry]:
    """One entry per live note of a project on the location that has no file yet, at the
    free path its title gives."""
    used = {e[3].document_id for e in entries if e[3] is not None}
    used |= {r["document_id"] for r in scan.records.values() if r["document_id"] is not None}
    found: list[_Entry] = []
    for doc_id, doc in sorted(scan.docs.items(), key=lambda kv: str(kv[0])):
        if doc_id in used or doc["deleted_at"] is not None or doc["kind"] != "text":
            continue
        root = roots.get(doc["project_id"])
        if root is None:
            continue
        path = await api.note_path(
            s, loc, root, doc["title"], document_id=doc_id, backend=None, taken=taken
        )
        found.append((path, None, None, _local_of(scan, doc_id, None), None))
    return found


def _decide(
    scan: _Scan,
    roots: Mapping[UUID, str],
    entries: list[_Entry],
    siblings: set[str],
    today: date,
) -> list[dict[str, Any]]:
    """The plan: each entry's decision, leaving out what writes nothing (bar renames) and
    a file already unindexed."""
    items = []
    for path, prev, listed, local, old in entries:
        stat = scan.files.get(listed) if listed is not None else None
        remote = _remote(stat, scan.hashes.get(path)) if stat is not None else None
        decision = decide_sync_action(
            prev, local, remote, path=path, today_local=today, siblings=frozenset(siblings)
        )
        if decision.conflict_path is not None:
            siblings.add(decision.conflict_path)
        record = scan.records.get(old or path)
        if decision.action == Action.NOOP and old is None:
            continue
        if (
            decision.action == Action.UNINDEX_ONLY
            and record
            and record["last_op"] == "unindex_only"
        ):
            continue
        project_id = scan.project_of.get(path) or _project_of(path, roots, local, scan)
        items.append(
            _item(
                path,
                decision,
                project_id=project_id,
                prev=prev,
                remote=remote,
                local=local,
                rename_from=old,
            )
        )
    return items


def _same_folder(old: str, moved: str, roots: Iterable[str]) -> bool:
    return any(old.startswith(r + "/") and moved.startswith(r + "/") for r in roots)


def _project_of(
    path: str, roots: Mapping[UUID, str], local: Local | None, scan: _Scan
) -> UUID | None:
    for project_id, root in roots.items():
        if path.startswith(root + "/"):
            return project_id
    if local is not None:
        doc = scan.docs.get(local.document_id)
        return doc["project_id"] if doc is not None else None
    return None


def _local_of(scan: _Scan, doc_id: UUID | None, record: Mapping[Any, Any] | None) -> Local | None:
    if doc_id is None:
        return None
    doc = scan.docs.get(doc_id)
    return _local(doc, record) if doc is not None else None


# --- 3. Applying one decision ---------------------------------------------------------------


async def _one(data: bytes) -> Any:
    yield data


async def _write(backend: StorageBackend, path: str, data: bytes, if_match: str | None) -> FileStat:
    """Write with the precondition; a failed precondition whose file already holds `data`
    counts as written (the write landed before a crash)."""
    try:
        return await backend.write(path, _one(data), if_match)
    except PreconditionFailed as exc:
        current = exc.current
        if current is not None and current.size == len(data):
            try:
                held = await spool(backend.read(path), limit=len(data))
            except (StorageError, AdapterError):
                raise exc from None
            if held == data:
                return current
        raise


async def _read(backend: StorageBackend, path: str) -> bytes:
    return await spool(backend.read(path))


class _Stale(Exception):  # noqa: N818  # a control-flow signal, not an error
    """The record or the Document changed since the plan: the next sync decides again."""


class _Apply:
    """One decision's work, inside one transaction on the location."""

    def __init__(  # the decision and its world
        self,
        s: AsyncSession,
        *,
        backend: StorageBackend,
        location_id: UUID,
        item: Mapping[Any, Any],
        record: RowMapping | None,
        doc: RowMapping | None,
    ) -> None:
        self.s, self.backend, self.location_id = s, backend, location_id
        self.path: str = item["path"]
        self.decision = SyncDecision.model_validate(item["decision"])
        self.prev = Prev.model_validate(item["prev"]) if item["prev"] else None
        self.remote = Remote.model_validate(item["remote"]) if item["remote"] else None
        self.local = Local.model_validate(item["local"]) if item["local"] else None
        self.project_id = UUID(item["project_id"]) if item["project_id"] else None
        self.record = record
        self.doc = doc
        self.extract: list[list[str]] = []  # [version id, location path] to extract

    # Records ---------------------------------------------------------------------------------

    async def record_stat(  # the record's columns
        self,
        stat: FileStat,
        data_hash: str,
        *,
        origin: Literal["tumnis", "external"],
        document_id: UUID | None,
        synced_version: int | None,
    ) -> None:
        await api.record_file(
            self.s,
            self.location_id,
            stat,
            content_hash=data_hash,
            origin=origin,
            document_id=document_id,
            synced_version=synced_version,
            last_op=self.decision.action.value,
        )

    async def drop_record(self, path: str | None = None) -> None:
        await self.s.execute(
            delete(_files).where(
                _files.c.location_id == self.location_id, _files.c.path == (path or self.path)
            )
        )

    async def review(self, doc_id: UUID | None, conflict_path: str | None = None) -> None:
        kind = self.decision.review_kind
        if kind is None:
            return
        payload = api.SyncReviewPayload(
            location_id=self.location_id,
            path=self.path,
            conflict_path=conflict_path,
            document_id=doc_id,
        )
        await tasks.add_review_item(
            kind,
            target=tasks.TargetRef(type="document", id=doc_id or self.location_id),
            project_id=self.project_id,
            payload=payload.model_dump(mode="json"),
            dedupe_key=f"{kind}:{self.location_id}:{self.path}",
            session=self.s,
        )

    # Documents -------------------------------------------------------------------------------

    async def new_document(self, path: str, data: bytes, *, taint: bool = True) -> None:
        """A Document for the outside file at `path`, recorded as external."""
        if self.project_id is None:
            raise _Stale
        doc_id, version, version_id = await api.create_file_document(
            self.s, self.project_id, self.location_id, path, data, source=api.FOLDER_SOURCE,
            tainted=taint,
        )  # fmt: skip
        stat = await self.backend.stat(path)
        if stat is None:
            raise _Stale
        await self.record_stat(
            stat,
            hashlib.sha256(data).hexdigest(),
            origin="external",
            document_id=doc_id,
            synced_version=version,
        )
        self.extract.append([str(version_id), path])

    async def from_folder(self, doc: Mapping[Any, Any], data: bytes, *, restore: bool) -> int:
        """The Document's next version from the folder's bytes; its new row version."""
        if doc["kind"] == "text":
            body: str | None = note_body(data.decode("utf-8", errors="replace"))
            stored = (body or "").encode()
            digest = hashlib.sha256(stored).digest()
        else:
            body, stored, digest = api.text_of(data), data, hashlib.sha256(data).digest()
        values: dict[str, Any] = {"body_md": body, "content_hash": digest}
        if restore:
            values["deleted_at"] = None
        version: int = await self.s.scalar(
            update(_documents)
            .where(_documents.c.id == doc["id"])
            .values(**values)
            .returning(_documents.c.version)
        )
        version_id = await api.add_version(self.s, doc["id"], stored, body)
        if doc["kind"] != "text":
            self.extract.append([str(version_id), self.path])
        return version

    async def snapshot(self, doc: Mapping[Any, Any]) -> None:
        """A note's text as a version, unless its latest version already holds it."""
        if doc["kind"] != "text":
            return
        body = (doc["body_md"] or "").encode()
        latest = await api.latest_version_hash(self.s, doc["id"])
        if latest != hashlib.sha256(body).digest():
            await api.add_version(self.s, doc["id"], body, doc["body_md"] or "")

    # Actions ---------------------------------------------------------------------------------

    async def run(self) -> None:  # one branch per action
        action = self.decision.action
        if action == Action.UPDATE_STAT:
            await self.update_stat()
        elif action == Action.CREATE_DOCUMENT:
            await self.new_document(
                self.path, await _read(self.backend, self.path),
                taint=self.decision.taint_new_document,
            )  # fmt: skip
        elif action in {Action.NEW_VERSION_FROM_FOLDER, Action.RESTORE_DOCUMENT}:
            await self.new_version(restore=action == Action.RESTORE_DOCUMENT)
        elif action in {Action.WRITE_THROUGH, Action.WRITE_NEW, Action.REWRITE_FROM_TUMNIS}:
            await self.write_tumnis()
        elif action == Action.ADOPT:
            await self.adopt()
        elif action == Action.CONFLICT_KEEP_BOTH:
            await self.conflict()
        elif action == Action.TRASH_DOCUMENT:
            await self.trash()
        elif action == Action.MOVE_TO_TUMNIS_TRASH:
            await self.move_to_trash()
        elif action == Action.UNINDEX_ONLY:
            await self.s.execute(
                update(_files).where(_files.c.id == self.record_id()).values(last_op="unindex_only")
            )
        elif action == Action.DELETE_AT_SOURCE:
            await self.delete_at_source()
        elif action == Action.FORGET:
            await self.drop_record()

    def record_id(self) -> UUID:
        if self.record is None:
            raise _Stale
        record_id: UUID = self.record["id"]
        return record_id

    def the_doc(self) -> RowMapping:
        if self.doc is None:
            raise _Stale
        return self.doc

    async def update_stat(self) -> None:
        assert self.remote is not None  # noqa: S101  # the table's row 6
        await self.s.execute(
            update(_files)
            .where(_files.c.id == self.record_id())
            .values(
                size=self.remote.size,
                mtime=self.remote.mtime,
                etag=self.remote.etag,
                last_op=Action.UPDATE_STAT.value,
            )
        )

    async def new_version(self, *, restore: bool) -> None:
        doc = self.the_doc()
        data = await _read(self.backend, self.path)
        version = await self.from_folder(doc, data, restore=restore)
        stat = await self.backend.stat(self.path)
        if stat is None:
            raise _Stale
        await self.record_stat(
            stat,
            hashlib.sha256(data).hexdigest(),
            origin=self.record["origin"] if self.record is not None else "external",
            document_id=doc["id"],
            synced_version=version,
        )
        await self.review(doc["id"])

    async def write_tumnis(self) -> None:
        doc = self.the_doc()
        data = local_bytes(doc)
        written = await _write(self.backend, self.path, data, self.decision.if_match)
        await self.snapshot(doc)
        await self.record_stat(
            written,
            hashlib.sha256(data).hexdigest(),
            origin=self.record["origin"] if self.record is not None else "tumnis",
            document_id=doc["id"],
            synced_version=doc["version"],
        )
        await self.review(doc["id"])

    async def adopt(self) -> None:
        doc = self.the_doc()
        assert self.remote is not None  # noqa: S101  # row 3
        assert self.local is not None  # noqa: S101
        stat = await self.backend.stat(self.path)
        if stat is None:
            raise _Stale
        await self.record_stat(
            stat,
            self.remote.content_hash or self.local.content_hash,
            origin=self.local.origin,
            document_id=doc["id"],
            synced_version=doc["version"],
        )

    async def free_conflict_path(self) -> str:
        wanted = self.decision.conflict_path
        assert wanted is not None  # noqa: S101  # every conflict names one
        taken = await api.taken_paths(self.s, self.location_id)
        return await api.free_path(wanted, taken, self.backend)

    async def conflict(self) -> None:
        doc = self.the_doc()
        target = await self.free_conflict_path()
        tumnis_bytes = local_bytes(doc)
        if self.prev is None:  # row 4: Tumnis's note moves aside, the outside file stays
            written = await _write(self.backend, target, tumnis_bytes, None)
            await self.snapshot(doc)
            await self.record_stat(
                written,
                hashlib.sha256(tumnis_bytes).hexdigest(),
                origin="tumnis",
                document_id=doc["id"],
                synced_version=doc["version"],
            )
            await self.new_document(self.path, await _read(self.backend, self.path))
        elif self.local is not None and self.local.origin == "tumnis" and self.decision.if_match:
            # Row 10: the outside copy is kept under the conflict name, Tumnis's written.
            outside = await _read(self.backend, self.path)
            await _write(self.backend, target, outside, None)
            await self.new_document(target, outside)
            written = await _write(self.backend, self.path, tumnis_bytes, self.decision.if_match)
            await self.snapshot(doc)
            await self.record_stat(
                written,
                hashlib.sha256(tumnis_bytes).hexdigest(),
                origin="tumnis",
                document_id=doc["id"],
                synced_version=doc["version"],
            )
        else:  # rows 9, 11: the outside file is untouched; Tumnis's text goes aside
            written = await _write(self.backend, target, tumnis_bytes, None)
            if self.project_id is None:
                raise _Stale
            copy_id, copy_version, _ = await api.create_file_document(
                self.s, self.project_id, self.location_id, target, tumnis_bytes,
                source=api.FOLDER_SOURCE,
            )  # fmt: skip
            await self.record_stat(
                written,
                hashlib.sha256(tumnis_bytes).hexdigest(),
                origin="tumnis",
                document_id=copy_id,
                synced_version=copy_version,
            )
            data = await _read(self.backend, self.path)
            version = await self.from_folder(doc, data, restore=False)
            stat = await self.backend.stat(self.path)
            if stat is None:
                raise _Stale
            await self.record_stat(
                stat,
                hashlib.sha256(data).hexdigest(),
                origin="external",
                document_id=doc["id"],
                synced_version=version,
            )
        await self.review(doc["id"], target)

    async def trash(self) -> None:
        doc = self.the_doc()
        await self.s.execute(
            update(_documents)
            .where(_documents.c.id == doc["id"], _documents.c.deleted_at.is_(None))
            .values(deleted_at=func.now())
        )
        await self.drop_record()
        await self.review(doc["id"])

    async def move_to_trash(self) -> None:
        stat = await self.backend.stat(self.path)
        if stat is not None:
            if not etag_equal(stat.etag, self.decision.if_match):
                raise _Stale  # changed since the plan: restored by the next sync
            root = next(
                (
                    f["root_path"]
                    for f in await self._folders()
                    if self.path.startswith(f["root_path"] + "/")
                ),
                None,
            )
            if root is None:
                raise _Stale
            inside = self.path[len(root) + 1 :]
            trash = f"{root}/{api.TUMNIS_DIR}/trash/{inside}"
            target = await api.free_path(trash, set(), self.backend)
            await self.backend.move(self.path, target)
        await self.drop_record()

    async def _folders(self) -> list[RowMapping]:
        return list(
            (
                await self.s.execute(
                    select(_folders).where(_folders.c.location_id == self.location_id)
                )
            )
            .mappings()
            .all()
        )

    async def delete_at_source(self) -> None:
        stat = await self.backend.stat(self.path)
        if stat is not None:
            if not etag_equal(stat.etag, self.decision.if_match):
                raise _Stale
            await self.backend.delete(self.path)
        await self.drop_record()


def _unchanged(item: Mapping[Any, Any], record: RowMapping | None, doc: RowMapping | None) -> bool:
    """The record and the Document still hold what the plan saw."""
    prev = item["prev"]
    if (prev is None) != (record is None):
        return False
    if prev is not None and record is not None:
        seen = (prev["etag"], prev["content_hash"], prev["synced_version"])
        now = (record["etag"], record["content_hash"], record["synced_version"])
        if seen != now or str(record["document_id"]) != str(prev["document_id"]):
            return False
    local = item["local"]
    if local is None:
        return True
    if doc is None:
        return False
    same: bool = doc["version"] == local["version"]
    return same and (doc["deleted_at"] is not None) == local["trashed"]


async def apply(workspace_id: str, location_id: str, item: Mapping[Any, Any]) -> list[list[str]]:
    """Apply one planned decision; the [version id, path] pairs to extract."""
    loc = UUID(location_id)
    async with tenant_session(_ctx(workspace_id)) as s:
        record = (
            (
                await s.execute(
                    select(_files)
                    .where(
                        _files.c.location_id == loc,
                        _files.c.path == (item["rename_from"] or item["path"]),
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .first()
        )
        doc = None
        if item["local"] is not None:
            doc = (
                (
                    await s.execute(
                        select(_documents)
                        .where(_documents.c.id == UUID(item["local"]["document_id"]))
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
        if not _unchanged(item, record, doc):
            return []
        if item["rename_from"] and record is not None:
            await s.execute(
                update(_files).where(_files.c.id == record["id"]).values(path=item["path"])
            )
        try:
            async with api.open_backend(s, loc, net=net()) as backend:
                work = _Apply(
                    s, backend=backend, location_id=loc, item=item, record=record, doc=doc
                )
                await work.run()
        except _Stale:
            await s.rollback()
            return []
        except (FileMissing, PreconditionFailed) as exc:  # changed since the plan
            log.info("folder sync left %s for the next run: %s", item["path"], exc)
            await s.rollback()
            return []
    return work.extract


# --- 4. Extraction and the end ----------------------------------------------------------------


async def request_extraction(workspace_id: str, requests: list[list[str]]) -> None:
    hook = _Config.extraction or _no_extraction
    for version_id, path in requests:
        await hook(UUID(workspace_id), UUID(version_id), path)


async def finish(workspace_id: str, location_id: str) -> None:
    async with tenant_session(_ctx(workspace_id)) as s:
        await s.execute(
            update(_locations)
            .where(_locations.c.id == UUID(location_id))
            .values(last_sync_at=clock().now())
        )


async def locations() -> list[tuple[str, str]]:
    """(workspace, location) for every live location of every workspace."""
    from tumnis.core import audit, db  # noqa: PLC0415

    async with db.app_sessionmaker()() as s, s.begin():
        workspaces = await audit.workspace_ids(s)
    found: list[tuple[str, str]] = []
    for workspace_id in workspaces:
        async with tenant_session(WorkspaceContext(workspace_id, SYSTEM_ACTOR)) as s:
            ids: Iterable[UUID] = await s.scalars(
                select(_locations.c.id).where(_locations.c.deleted_at.is_(None))
            )
            found += [(str(workspace_id), str(i)) for i in ids]
    return found


async def watched_roots() -> list[tuple[str, str, str]]:
    """(workspace, location, root) for every live server path on a local disk: what
    `local_watch` watches (a network share's events are unreliable, FR-15.12, so its
    15-minute scan is all it gets)."""
    from tumnis.core import audit, db  # noqa: PLC0415

    async with db.app_sessionmaker()() as s, s.begin():
        workspaces = await audit.workspace_ids(s)
    found: list[tuple[str, str, str]] = []
    for workspace_id in workspaces:
        async with tenant_session(WorkspaceContext(workspace_id, SYSTEM_ACTOR)) as s:
            rows = await s.execute(
                select(_locations.c.id, _locations.c.root, _locations.c.capabilities).where(
                    _locations.c.deleted_at.is_(None), _locations.c.kind == "server_path"
                )
            )
            found += [
                (str(workspace_id), str(row.id), row.root)
                for row in rows
                if not (row.capabilities or {}).get("network_fs")
            ]
    return found


def watched_location(path: str, roots: Iterable[tuple[str, str, str]]) -> tuple[str, str] | None:
    """The (workspace, location) whose project folders hold the changed `path`; None for a
    change at the root itself, Tumnis's own (`.tumnis/`, temp files) or an OS dropping."""
    for workspace_id, location_id, root in roots:
        prefix = root.rstrip("/") + "/"
        if path.startswith(prefix):
            _folder, _, inside = path[len(prefix) :].partition("/")
            return None if not inside or ignored(inside) else (workspace_id, location_id)
    return None
