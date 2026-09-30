"""Archiving a project's folder (P2-18, FR-15.6, FR-15.12): the steps `projects.workflows`
runs through its archive hooks (registered at the bottom; knowledge.workflows imports this
module in the worker).

- A Tumnis-made folder is packed into one tar + zstd file on its location, outside the
  folder (`.tumnis/archives/<folder id>.tar.zst` at the location's root), with a manifest
  (relative path, size, sha256 per file). The pack is read back and its manifest checked
  against the folder's before any file is deleted, and deletes go through the storage
  backend, only for this folder Tumnis made. Its `folder_files` records stay: unarchive
  puts every file back byte for byte, so they still hold. A folder whose pack would pass
  the 50 MiB file limit (`MAX_FILE_BYTES`) is archived index-only instead.
- An existing folder (and the fallback) stays where it is, untouched: only its index
  moves, the folder's `folder_files` records and the chunks of the project's documents,
  into `archived_blobs` (module `knowledge`), and comes back on unarchive.

While a project is archived (or on its way in or out) the folder sync leaves its folder
alone (`sync._load`). Every step is idempotent: a replayed pack finds the pack written
(it is written once), a replayed unpack finds the files back.
"""

import io
import json
import tarfile
from collections.abc import AsyncIterator
from typing import Any, Final
from uuid import UUID

import structlog
import zstandard
from sqlalchemy import RowMapping, Table, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import archive_blobs as blobs
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.knowledge import api, sync
from tumnis.modules.knowledge.models import Chunk, FolderFile, ProjectFolder
from tumnis.modules.knowledge.storage import MAX_FILE_BYTES, StorageBackend, spool
from tumnis.modules.knowledge.storage import NotFound as FileMissing
from tumnis.modules.projects import api as projects

_log = structlog.get_logger(__name__)

MODULE: Final = "knowledge"
FOLDER_PACK: Final = "folder_pack"  # blob kind: where a folder's pack is and its manifest
FOLDER_FILES: Final = "folder_files"  # blob kind: an index-only folder's records
CHUNKS: Final = "chunks"  # blob kind: the chunks of the project's documents
ARCHIVES_DIR: Final = f"{api.TUMNIS_DIR}/archives"
PACK_LEVEL: Final = 10

_folders: Table = ProjectFolder.__table__  # type: ignore[assignment]
_files: Table = FolderFile.__table__  # type: ignore[assignment]
_chunks: Table = Chunk.__table__  # type: ignore[assignment]


class PackMismatch(RuntimeError):  # noqa: N818  # the archive's word
    """A folder pack whose content does not match the folder's manifest."""


def _ctx(workspace_id: UUID) -> WorkspaceContext:
    return WorkspaceContext(workspace_id, SYSTEM_ACTOR)


def pack_path(folder_id: UUID) -> str:
    return f"{ARCHIVES_DIR}/{folder_id}.tar.zst"


async def _folder(s: AsyncSession, project_id: UUID) -> RowMapping | None:
    return (
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


async def _one(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _tree(backend: StorageBackend, root: str) -> dict[str, bytes]:
    """Every file under the folder (relative path -> bytes)."""
    found: dict[str, bytes] = {}
    cursor: str | None = None
    while True:
        try:
            page = await backend.list(f"{root}/", cursor)
        except FileMissing:  # the folder was never made on the location
            return found
        for stat in page.items:
            found[stat.path.removeprefix(f"{root}/")] = await spool(backend.read(stat.path))
        cursor = page.next_cursor
        if cursor is None:
            return found


def _pack(files: dict[str, bytes]) -> bytes:
    """A tar of the files (sorted, mode 0644), through zstd."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for rel in sorted(files):
            info = tarfile.TarInfo(rel)
            info.size = len(files[rel])
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(files[rel]))
    return zstandard.ZstdCompressor(level=PACK_LEVEL).compress(raw.getvalue())


def _unpack(packed: bytes) -> dict[str, bytes]:
    """The regular files of a pack, by name. Nothing is extracted to disk: each member is
    read into memory and written back through the storage backend."""
    raw = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(packed)).read()
    out: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            out[member.name] = extracted.read()
    return out


# --- archive ---------------------------------------------------------------------------------


async def archive_folder(workspace_id: UUID, project_id: UUID) -> None:
    async with tenant_session(_ctx(workspace_id)) as s:
        folder = await _folder(s, project_id)
        if folder is None:
            return
        if folder["mode"] == "tumnis_made" and await _pack_folder(s, folder, project_id):
            return
        await _archive_index(s, folder, project_id)


async def _pack_folder(s: AsyncSession, folder: RowMapping, project_id: UUID) -> bool:
    """Pack, verify, then delete the folder's files; False when the pack would be too big
    (the caller archives the index instead)."""
    path = pack_path(folder["id"])
    root = folder["root_path"]
    async with api.open_backend(s, folder["location_id"], net=sync.net()) as backend:
        files = await _tree(backend, root)
        existing = await backend.stat(path)
        if existing is None:
            if not files:
                return True  # an empty folder: nothing to keep
            packed = _pack(files)
            if len(packed) > MAX_FILE_BYTES:
                _log.warning("folder_pack_too_large", project_id=str(project_id))
                return False
            await backend.ensure_folder(ARCHIVES_DIR)
            await backend.write(path, _one(packed), None)
        kept = _unpack(await spool(backend.read(path)))
        if files and blobs.Manifest.of_files(kept) != blobs.Manifest.of_files(files):
            raise PackMismatch(f"the pack of {root} does not match its files")
        manifest = blobs.Manifest.of_files(kept)
        await blobs.put_blob(
            s,
            module=MODULE,
            kind=FOLDER_PACK,
            project_id=project_id,
            ref=str(folder["id"]),
            raw=json.dumps(
                {"path": path, "root": root, "manifest": manifest.entries}, sort_keys=True
            ).encode(),
        )
        for rel in sorted(files):
            await backend.delete(f"{root}/{rel}")
    return True


async def _archive_index(s: AsyncSession, folder: RowMapping, project_id: UUID) -> None:
    """The folder's `folder_files` records and the project's chunks into blobs, deleted in
    the same transaction."""
    records = await blobs.snapshot_rows(
        s,
        "folder_files",
        "t.location_id = :loc AND starts_with(t.path, :prefix) AND t.deleted_at IS NULL",
        {"loc": folder["location_id"], "prefix": f"{folder['root_path']}/"},
    )
    if records:
        await blobs.put_blob(
            s,
            module=MODULE,
            kind=FOLDER_FILES,
            project_id=project_id,
            ref=str(folder["id"]),
            raw=blobs.encode_rows(records),
        )
        await s.execute(delete(_files).where(_files.c.id.in_([UUID(r["id"]) for r in records])))
    chunks = await blobs.snapshot_rows(
        s,
        "chunks",
        "t.document_id IN (SELECT d.id FROM documents d WHERE d.project_id = :p)",
        {"p": project_id},
    )
    if chunks:
        await blobs.put_blob(
            s,
            module=MODULE,
            kind=CHUNKS,
            project_id=project_id,
            ref="project",
            raw=blobs.encode_rows(chunks),
        )
        await s.execute(delete(_chunks).where(_chunks.c.id.in_([UUID(c["id"]) for c in chunks])))


# --- unarchive -------------------------------------------------------------------------------


async def unarchive_folder(workspace_id: UUID, project_id: UUID) -> None:
    async with tenant_session(_ctx(workspace_id)) as s:
        for _ref, raw in await blobs.blobs(
            s, module=MODULE, kind=FOLDER_PACK, project_id=project_id
        ):
            await _unpack_folder(s, project_id, json.loads(raw))
        await blobs.delete_blobs(s, module=MODULE, kind=FOLDER_PACK, project_id=project_id)
        for kind, table in ((FOLDER_FILES, "folder_files"), (CHUNKS, "chunks")):
            for _ref, raw in await blobs.blobs(s, module=MODULE, kind=kind, project_id=project_id):
                await blobs.restore_rows(s, table, blobs.decode_rows(raw))
            await blobs.delete_blobs(s, module=MODULE, kind=kind, project_id=project_id)


async def _unpack_folder(s: AsyncSession, project_id: UUID, facts: dict[str, Any]) -> None:
    """Every packed file back where it was (a file already there with the same bytes is
    left alone), the tree checked against the manifest, then the pack deleted."""
    folder = await _folder(s, project_id)
    if folder is None:
        return
    root, path = facts["root"], facts["path"]
    want = blobs.Manifest.of(tuple(entry) for entry in facts["manifest"])
    async with api.open_backend(s, folder["location_id"], net=sync.net()) as backend:
        if await backend.stat(path) is not None:
            files = _unpack(await spool(backend.read(path)))
            if blobs.Manifest.of_files(files) != want:
                raise PackMismatch(f"the pack of {root} does not match its manifest")
            for rel, data in sorted(files.items()):
                target = f"{root}/{rel}"
                current = await backend.stat(target)
                if current is not None:
                    if await spool(backend.read(target)) == data:
                        continue
                    await backend.write(target, _one(data), current.etag)
                else:
                    await backend.write(target, _one(data), None)
        back = await _tree(backend, root)
        got = blobs.Manifest.of_files({rel: back[rel] for rel, _, _ in want.entries if rel in back})
        if got != want:
            raise PackMismatch(f"{root} does not match its manifest after the unpack")
        await backend.delete(path)


# --- purge -----------------------------------------------------------------------------------


async def purge_archive(workspace_id: UUID, project_id: UUID) -> None:
    """A purged project: its pack deleted from the location, and the knowledge blobs."""
    async with tenant_session(_ctx(workspace_id)) as s:
        packs = await blobs.blobs(s, module=MODULE, kind=FOLDER_PACK, project_id=project_id)
        folder = (
            await s.execute(
                select(_folders.c.location_id).where(_folders.c.project_id == project_id)
            )
        ).first()
        if packs and folder is not None:
            async with api.open_backend(s, folder.location_id, net=sync.net()) as backend:
                for _ref, raw in packs:
                    await backend.delete(json.loads(raw)["path"])
        await blobs.delete_blobs(s, module=MODULE, project_id=project_id)


for _name, _hook in {
    "knowledge.archive_folder": archive_folder,
    "knowledge.unarchive_folder": unarchive_folder,
    "knowledge.purge_archive": purge_archive,
}.items():
    projects.register_archive_hook(_name, _hook)
