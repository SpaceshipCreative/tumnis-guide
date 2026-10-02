"""Moving a project's folder to another location (P3-14, FR-15.12): copy, verify by hash,
switch, keep the old copy.

What `knowledge_move_project_folder` (workflows.py) runs, one call per DBOS step:

1. `begin`: check the target (online, a path neither the project's own folder nor another
   project's overlaps, no other move of the project still `copying`) and record the move
   (`folder_moves`, `copying`) under the id the workflow gives it, so a re-run step
   returns the same move.
2. `list_source`: every file in the project's folder with its sha256 and etag (Tumnis's
   own `.tumnis/` or `Tumnis/` included; temp files left out), recorded by DBOS.
3. `copy_batch`: batches of `BATCH` files (plan default 100), create-only on the target. A
   file already there with the same bytes (a batch re-run after a kill) is left as it is;
   one with other bytes fails the move (`target_conflict`).
4. `verify`: read every copy back and compare its hash with the source's; any mismatch
   fails the move (`hash_mismatch`) and nothing is switched.
5. `switch`: one transaction, with the move and the project's folder locked: unless the
   folder still is the move's source and the source still holds exactly the listed files
   (same etags), the move fails (`changed_during_move`) and nothing is switched. Else the
   project's folder, its file records (origin and Document kept, the target's size, mtime
   and etag), its Documents' location and its queued writes move to the target; the move
   is `switched` with the old copy kept, and a `folder_move_old_copy` review item asks the
   user about it. Run again after it committed, it changes nothing.

The source is never written, moved or deleted by the move job.
"""

import hashlib
import hmac
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import RowMapping, Table, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.errors import ProblemError
from tumnis.core.live import mark_changed
from tumnis.core.net import NetPolicy
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.core.versioning import NotFound
from tumnis.modules.knowledge import api, sync
from tumnis.modules.knowledge.models import (
    Document,
    FolderFile,
    FolderMove,
    PendingWrite,
    ProjectFolder,
    StorageLocation,
)
from tumnis.modules.knowledge.rules import PathRejected, safe_rel_path
from tumnis.modules.knowledge.storage import (
    FileStat,
    PreconditionFailed,
    StorageBackend,
    StorageError,
)
from tumnis.modules.tasks import api as tasks

Fault = Callable[[str, bytes], bytes]

BATCH: Final = 100  # plan default
TMP_PREFIX: Final = ".tumnis-tmp-"
OLD_COPY_REVIEW_KIND: Final = "folder_move_old_copy"

_moves: Table = FolderMove.__table__  # type: ignore[assignment]
_folders: Table = ProjectFolder.__table__  # type: ignore[assignment]
_files: Table = FolderFile.__table__  # type: ignore[assignment]
_documents: Table = Document.__table__  # type: ignore[assignment]
_pending: Table = PendingWrite.__table__  # type: ignore[assignment]
_locations: Table = StorageLocation.__table__  # type: ignore[assignment]

_fault: list[Fault | None] = [None]


class OldCopyReviewPayload(BaseModel):
    """A moved project's old folder is still on its old location: `accept` keeps it."""

    move_id: UUID
    location_id: UUID
    path: str


tasks.register_review_kind(
    tasks.ReviewKindSpec(
        kind=OLD_COPY_REVIEW_KIND,
        owner_module="knowledge",
        payload_schema=OldCopyReviewPayload,
        actions=("accept", "snooze"),
        impact_scope="project",
    )
)


def use_fault(fn: Fault | None) -> Fault | None:
    """Test-only: pass every copied file's bytes through `fn(path, data)` (fault
    injection); returns the previous hook. None removes it."""
    previous = _fault[0]
    _fault[0] = fn
    return previous


def _net_policy() -> NetPolicy:
    """The folder sync's net policy (`sync.configure`, else the deployment's)."""
    return sync.net()


def _ctx(workspace_id: str) -> WorkspaceContext:
    return WorkspaceContext(UUID(workspace_id), SYSTEM_ACTOR)


def _overlaps(a: str, b: str) -> bool:
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


async def _read_all(backend: StorageBackend, path: str) -> bytes:
    return b"".join([chunk async for chunk in backend.read(path)])


async def _hash_file(backend: StorageBackend, path: str) -> str:
    """sha256 hex of a stored file, read as a stream (a content hash, no secret)."""
    digest = hashlib.sha256()
    async for chunk in backend.read(path):
        digest.update(chunk)
    return digest.hexdigest()


async def _one(data: bytes) -> AsyncIterator[bytes]:
    yield data


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()  # a content hash, no secret


async def _check(
    s: AsyncSession, project: UUID, target_id: UUID, to_path: str, *, own: UUID | None = None
) -> tuple[api.ProjectFolderOut, str] | str:
    """The project's folder and the target root, or the code of why the target cannot
    take the folder (`path_rejected`, `location_offline`, `same_folder`, `folder_taken`,
    `move_in_progress`: a move other than `own` is still copying the folder)."""
    folder = await api.get_project_folder(s, project)
    copying = await s.scalar(
        select(_moves.c.id)
        .where(
            _moves.c.project_id == project,
            _moves.c.status == "copying",
            _moves.c.deleted_at.is_(None),
            *([_moves.c.id != own] if own is not None else []),
        )
        .limit(1)
    )
    if copying is not None:
        return "move_in_progress"
    try:
        to_root = safe_rel_path(to_path.strip("/"))
    except PathRejected:
        return "path_rejected"
    target = await s.scalar(
        select(_locations.c.status).where(
            _locations.c.id == target_id, _locations.c.deleted_at.is_(None)
        )
    )
    if target != "online":
        return "location_offline"
    if folder.location_id == target_id and _overlaps(folder.root_path, to_root):
        return "same_folder"
    others: list[str] = list(
        await s.scalars(
            select(_folders.c.root_path).where(
                _folders.c.location_id == target_id,
                _folders.c.project_id != project,
                _folders.c.deleted_at.is_(None),
            )
        )
    )
    if any(_overlaps(other, to_root) for other in others):
        return "folder_taken"
    return folder, to_root


async def precheck(s: AsyncSession, project_id: UUID, to_location: UUID, to_path: str) -> None:
    """The route's check before it queues a move: the problem `begin` would end the move
    with (409, or 422 for an unsafe path), so a refused move never answers 202. The rows
    are found first: 404 for a project without a folder or a location not in view."""
    found = await s.scalar(
        select(_locations.c.id).where(
            _locations.c.id == to_location, _locations.c.deleted_at.is_(None)
        )
    )
    await api.get_project_folder(s, project_id)
    if found is None:
        raise NotFound("storage_locations", to_location)
    checked = await _check(s, project_id, to_location, to_path)
    if isinstance(checked, str):
        raise refuse(checked)


def _record(row: RowMapping) -> dict[str, Any]:
    return {
        "move_id": str(row["id"]),
        "project_id": str(row["project_id"]),
        "from_location": str(row["from_location"]),
        "from_path": row["from_path"],
        "to_location": str(row["to_location"]),
        "to_path": row["to_path"],
    }


async def _lock_folder(s: AsyncSession, project: UUID) -> RowMapping | None:
    """The project's folder row, locked to the end of the transaction: moves of one
    project begin and switch one at a time."""
    return (
        (
            await s.execute(
                select(_folders.c.location_id, _folders.c.root_path)
                .where(_folders.c.project_id == project, _folders.c.deleted_at.is_(None))
                .with_for_update()
            )
        )
        .mappings()
        .first()
    )


async def begin(
    workspace_id: str, project_id: str, to_location: str, to_path: str, *, move_id: UUID
) -> dict[str, Any]:
    """The move's record, or {"error": code} when the target cannot take the folder
    (checked again here: the location may have gone offline since the route's check).
    `move_id` comes from the workflow: a step run again returns the move it recorded."""
    project, target_id = UUID(project_id), UUID(to_location)
    async with tenant_session(_ctx(workspace_id)) as s:
        await _lock_folder(s, project)
        recorded = (
            (await s.execute(select(*_moves.c).where(_moves.c.id == move_id))).mappings().first()
        )
        if recorded is not None:
            return _record(recorded)
        checked = await _check(s, project, target_id, to_path, own=move_id)
        if isinstance(checked, str):
            return {"error": checked}
        folder, to_root = checked
        row = (
            (
                await s.execute(
                    insert(_moves)
                    .values(
                        id=move_id,
                        project_id=project,
                        from_location=folder.location_id,
                        from_path=folder.root_path,
                        to_location=target_id,
                        to_path=to_root,
                    )
                    .returning(*_moves.c)
                )
            )
            .mappings()
            .one()
        )
    return _record(row)


async def _source_files(backend: StorageBackend, root: str) -> AsyncIterator[FileStat]:
    """Every file in the folder `root`, temp files left out."""
    cursor: str | None = None
    while True:
        page = await backend.list(root + "/", cursor)
        for stat in page.items:
            if not stat.path.rsplit("/", 1)[-1].startswith(TMP_PREFIX):
                yield stat
        cursor = page.next_cursor
        if cursor is None:
            return


async def list_source(workspace_id: str, move: Mapping[str, Any]) -> list[list[str]]:
    """[path inside the folder, sha256 hex, etag] of every file in the source folder."""
    root = move["from_path"]
    found: list[list[str]] = []
    async with (
        tenant_session(_ctx(workspace_id)) as s,
        api.open_backend(s, UUID(move["from_location"]), net=_net_policy()) as backend,
    ):
        async for stat in _source_files(backend, root):
            inside = stat.path[len(root) + 1 :]
            found.append([inside, await _hash_file(backend, stat.path), stat.etag])
    return sorted(found)


async def copy_batch(
    workspace_id: str, move: Mapping[str, Any], batch: list[list[str]]
) -> str | None:
    """Copy one batch, create-only; a copy already there with the source's bytes stays.
    `target_conflict` when the target already holds other bytes at a copy's path (a
    retry would not change that), else None."""
    src_root, dst_root = move["from_path"], move["to_path"]
    async with (
        tenant_session(_ctx(workspace_id)) as s,
        api.open_backend(s, UUID(move["from_location"]), net=_net_policy()) as source,
        api.open_backend(s, UUID(move["to_location"]), net=_net_policy()) as target,
    ):
        for inside, *_listed in batch:
            data = await _read_all(source, f"{src_root}/{inside}")
            path = f"{dst_root}/{inside}"
            hook = _fault[0]
            if hook is not None:
                data = hook(path, data)
            try:
                await target.write(path, _one(data), None)
            except PreconditionFailed:
                if await _hash_file(target, path) != _sha(data):
                    return "target_conflict"
    return None


async def verify(
    workspace_id: str, move: Mapping[str, Any], files: list[list[str]]
) -> dict[str, list[Any]] | None:
    """{path inside: [size, mtime iso, etag]} of every copy when each copy's hash is the
    source's; None at the first mismatch."""
    dst_root = move["to_path"]
    stats: dict[str, list[Any]] = {}
    async with (
        tenant_session(_ctx(workspace_id)) as s,
        api.open_backend(s, UUID(move["to_location"]), net=_net_policy()) as target,
    ):
        for inside, digest, *_etag in files:
            path = f"{dst_root}/{inside}"
            try:
                held = await _hash_file(target, path)
            except StorageError:
                return None
            stat = await target.stat(path)
            if stat is None or not hmac.compare_digest(held, digest):
                return None
            stats[inside] = [stat.size, stat.mtime.isoformat(), stat.etag]
    return stats


async def fail(workspace_id: str, move_id: str, reason: str) -> dict[str, str | None]:
    """Mark the move `failed` if it is still `copying` (decision 91: a switched move, or one
    already failed with its own reason, is left as it is); the move's status and reason
    afterwards."""
    async with tenant_session(_ctx(workspace_id)) as s:
        await s.execute(
            update(_moves)
            .where(_moves.c.id == UUID(move_id), _moves.c.status == "copying")
            .values(status="failed", reason=reason, updated_at=func.now())
        )
        status, now_reason = (
            await s.execute(
                select(_moves.c.status, _moves.c.reason).where(_moves.c.id == UUID(move_id))
            )
        ).one()
    return {"status": status, "reason": now_reason}


CHANGED: Final = "changed_during_move"


async def _source_changed(s: AsyncSession, move: Mapping[str, Any], files: list[list[str]]) -> bool:
    """Whether the project's folder (locked) is no longer the move's source, or the
    source no longer holds exactly the listed files with their listed etags."""
    folder = await _lock_folder(s, UUID(move["project_id"]))
    src_loc, src_root = UUID(move["from_location"]), move["from_path"]
    if folder is None or (folder["location_id"], folder["root_path"]) != (src_loc, src_root):
        return True
    listed = {inside: etag for inside, _digest, etag in files}
    async with api.open_backend(s, src_loc, net=_net_policy()) as backend:
        now = {
            stat.path[len(src_root) + 1 :]: stat.etag
            async for stat in _source_files(backend, src_root)
        }
    return now != listed


async def switch(
    workspace_id: str,
    move: Mapping[str, Any],
    files: list[list[str]],
    stats: Mapping[str, list[Any]],
) -> str | None:
    """Point the project at the copy, in one transaction (see the module docstring): None
    once the move is switched, else why it failed (`changed_during_move`)."""
    project, move_id = UUID(move["project_id"]), UUID(move["move_id"])
    src_loc, dst_loc = UUID(move["from_location"]), UUID(move["to_location"])
    src_root, dst_root = move["from_path"], move["to_path"]
    async with tenant_session(_ctx(workspace_id)) as s:
        status, reason = (
            await s.execute(
                select(_moves.c.status, _moves.c.reason)
                .where(_moves.c.id == move_id)
                .with_for_update()
            )
        ).one()
        if status == "switched":
            return None
        if status == "failed":
            return str(reason)
        if await _source_changed(s, move, files):
            await s.execute(
                update(_moves)
                .where(_moves.c.id == move_id)
                .values(status="failed", reason=CHANGED, updated_at=func.now())
            )
            return CHANGED
        await s.execute(
            update(_folders)
            .where(_folders.c.project_id == project, _folders.c.deleted_at.is_(None))
            .values(location_id=dst_loc, root_path=dst_root, version=_folders.c.version + 1)
        )
        await _move_records(
            s, src_loc=src_loc, dst_loc=dst_loc, src_root=src_root, dst_root=dst_root, stats=stats
        )
        await s.execute(
            update(_documents)
            .where(_documents.c.project_id == project, _documents.c.storage_location_id == src_loc)
            .values(storage_location_id=dst_loc)
        )
        prefix = src_root + "/"
        queued = await s.execute(
            select(_pending.c.id, _pending.c.path).where(
                _pending.c.location_id == src_loc,
                _pending.c.path.startswith(prefix, autoescape=True),
                _pending.c.deleted_at.is_(None),
            )
        )
        for pending_id, path in queued.all():
            await s.execute(
                update(_pending)
                .where(_pending.c.id == pending_id)
                .values(location_id=dst_loc, path=dst_root + path[len(src_root) :])
            )
        await s.execute(
            update(_moves)
            .where(_moves.c.id == move_id)
            .values(status="switched", verified_count=len(stats), old_kept=True)
        )
        await tasks.add_review_item(
            OLD_COPY_REVIEW_KIND,
            target=tasks.TargetRef(type="project", id=project),
            project_id=project,
            payload=OldCopyReviewPayload(
                move_id=move_id, location_id=src_loc, path=src_root
            ).model_dump(mode="json"),
            dedupe_key=f"{OLD_COPY_REVIEW_KIND}:{move['move_id']}",
            session=s,
        )
        mark_changed(s, "project", project)
    return None


async def _move_records(
    s: AsyncSession,
    *,
    src_loc: UUID,
    dst_loc: UUID,
    src_root: str,
    dst_root: str,
    stats: Mapping[str, list[Any]],
) -> None:
    rows = await s.execute(
        select(_files.c.id, _files.c.path).where(
            _files.c.location_id == src_loc,
            _files.c.path.startswith(src_root + "/", autoescape=True),
        )
    )
    for record_id, path in rows.all():
        inside = path[len(src_root) + 1 :]
        values: dict[str, Any] = {"location_id": dst_loc, "path": f"{dst_root}/{inside}"}
        if inside in stats:
            size, mtime, etag = stats[inside]
            values |= {"size": size, "mtime": datetime.fromisoformat(mtime), "etag": etag}
        await s.execute(update(_files).where(_files.c.id == record_id).values(**values))


def refuse(error: str) -> ProblemError:
    """The problem a refused move start maps to (the api route)."""
    status = 422 if error == "path_rejected" else 409
    return ProblemError(status, error, "The folder cannot be moved there.")
