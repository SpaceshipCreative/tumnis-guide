"""Syncing an Obsidian vault into the knowledge base (P3-12, FR-15.10, FR-15.8, SAF-1).

`sync_vault(ctx, connection_id, reader, mapping)` reads the vault through a `VaultReader`
(the read-only mounted folder, or the Git clone after `refresh`) and makes the knowledge
base match it. The vault is the source: nothing here writes to it, and the Documents it
makes are read-only in Tumnis (`api.edit_document` and `api.trash` answer 409
`read_only_source`).

One scan:

1. The templates folder comes from `.obsidian/templates.json` (else `Templates/`); it,
   `.obsidian/`, `.trash/`, `.git/` and the user's excludes are pruned from the listing,
   so nothing under them is opened.
2. Every note (`*.md`) is read and parsed. Its project comes from `map_note_to_project`
   (frontmatter key, then `#tumnis/<project>` tag, then the longest mapped folder; a
   project is named by `project_slug` of its name); unmapped notes go to the workspace
   knowledge base or are skipped (`unmapped = "ignore"`).
3. A note's Document is found by its vault path (`documents.path`, also its
   `external_id`), live or in the trash (a note back at its old path is restored). A note
   at a new path whose file hash (`source_revision`, sha256 hex) is that of a Document whose
   path is gone keeps that Document: a rename, no new version. A changed body is a new
   version (`api.write_synced_text`: versioned, chunked, `document.changed`); a change to
   the title, tags or project alone updates the row. Nothing that is unchanged is written
   (the touch trigger would bump `version`).
4. Trust is set when the Document is made: untrusted and tainted under the clippings
   folder, else trusted. A later scan only ever lowers it (a note moved into the clippings
   folder); taint is never cleared, and a person's trust decision is kept.
5. Attachments embedded by a synced note (`![[diagram.png]]`) become file Documents in the
   first embedding note's project: untrusted, `pending_scan`, their bytes spooled to
   `<spool>/<version_id>` and their extraction requested through `extract` once the scan
   commits (P1-16's pipeline with `source = "vault"`: scanned, sniffed, converted, never
   placed in a project folder). A changed attachment gets a new version the same way. An
   unchanged one still `pending_scan` an hour after its request is requested again (a
   request lost between the commit and the enqueue; a live one is not repeated).
6. Documents whose file is gone, or that nothing embeds any more, go to the trash.
7. `document_links` hold each note's links and embeds as written (`to_target`), resolved
   by Obsidian's shortest-path rule against this scan's listing every time, so a link to
   a note that did not exist yet resolves once it does. A note's rows are replaced only
   when they differ.

The vault is plain files, so no Obsidian process is involved (FR-15.10).
"""

import asyncio
import hashlib
import posixpath
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.schema import Table

from tumnis.core.clock import Clock, SystemClock
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.modules.knowledge import api, pipeline
from tumnis.modules.knowledge.adapters.obsidian.port import FileStat, VaultReader
from tumnis.modules.knowledge.models import Document, DocumentLink
from tumnis.modules.knowledge.obsidian.parse import Link, ParsedNote, parse_note
from tumnis.modules.knowledge.obsidian.rules import (
    TEMPLATES_CONFIG,
    PathIndex,
    VaultMapping,
    is_excluded,
    is_note_path,
    map_note_to_project,
    note_trust,
    project_slug,
    resolve_link,
    templates_folder,
)
from tumnis.modules.knowledge.storage import NotFound, StorageError
from tumnis.modules.projects import api as projects

__all__ = [
    "SOURCE",
    "ExtractHook",
    "PreviewRow",
    "VaultSyncReport",
    "preview_mapping",
    "register_extraction",
    "sync_vault",
]

SOURCE: Final = "knowledge:obsidian"  # `documents.source`, as `connection_source` spells it
MAX_TAGS: Final = api.MAX_TAGS
# An unchanged attachment still pending this long after its extraction was requested is
# requested again (the request may have been lost between the commit and the enqueue).
EXTRACTION_RETRY_AFTER: Final = timedelta(hours=1)
MAX_TAG_CHARS: Final = api.MAX_TAG_CHARS

ExtractHook = Callable[[UUID, UUID, str], Awaitable[None]]  # (workspace, version, path)

_documents: Table = Document.__table__  # type: ignore[assignment]
_links: Table = DocumentLink.__table__  # type: ignore[assignment]
_hooks: dict[str, ExtractHook] = {}


def register_extraction(hook: ExtractHook) -> None:
    """The default extraction request for attachments (`knowledge.workflows` registers
    `enqueue_vault_extraction`; the sync module does not import the workflows)."""
    _hooks["extract"] = hook


@dataclass
class VaultSyncReport:
    created: int = 0
    changed: int = 0
    renamed: int = 0
    restored: int = 0
    trashed: int = 0
    attachments: int = 0  # attachment versions made (each with its extraction requested)
    skipped: list[str] = field(default_factory=list)  # unreadable files, left as they were


@dataclass(frozen=True)
class _Note:
    stat: FileStat
    parsed: ParsedNote
    project_id: UUID | None
    untrusted: bool


@dataclass
class _Scan:
    """What one scan knows: the listing, the connection's Documents by path, and the
    notes it read."""

    listing: dict[str, FileStat]
    docs: dict[str, dict[str, Any]]
    notes: dict[str, _Note] = field(default_factory=dict)
    kept: set[str] = field(default_factory=set)  # paths whose Document stays as it is
    report: VaultSyncReport = field(default_factory=VaultSyncReport)
    extractions: list[tuple[UUID, str]] = field(default_factory=list)
    index: PathIndex = field(init=False)  # the listing indexed once for resolve_link

    def __post_init__(self) -> None:
        self.index = PathIndex(self.listing)


async def sync_vault(  # the connection, its reader and mapping, and the hooks
    ctx: WorkspaceContext,
    connection_id: UUID,
    reader: VaultReader,
    mapping: VaultMapping,
    *,
    extract: ExtractHook | None = None,
    clock: Clock | None = None,
) -> VaultSyncReport:
    """One scan of the vault into the workspace's knowledge base (module docstring)."""
    request = extract or _hooks.get("extract")
    if request is None:
        raise RuntimeError("no extraction hook: import knowledge.workflows or pass extract=")
    now = (clock or SystemClock()).now()
    await reader.refresh()
    templates = templates_folder(await _config_text(reader))
    listed = await reader.list_files(lambda path: is_excluded(path, mapping, templates))
    async with tenant_session(ctx) as s:
        known = await _known_projects(s)
        docs = await _connection_docs(s, connection_id)
    scan = _Scan(listing={item.path: item for item in listed}, docs=docs)
    await _read_notes(reader, scan, mapping, known)
    async with tenant_session(ctx) as s:
        await _apply_notes(s, scan, connection_id, now)
        await _apply_attachments(s, reader, scan, connection_id, now)
        await _trash_missing(s, scan)
        await _write_links(s, scan)
    for version_id, path in scan.extractions:
        await request(ctx.workspace_id, version_id, path)
    return scan.report


@dataclass(frozen=True)
class PreviewRow:
    """One note as the mapping would sync it: its project (None: the workspace knowledge
    base), skipped (`unmapped = "ignore"`), and untrusted (under the clippings folder)."""

    path: str
    project_id: UUID | None
    ignored: bool
    untrusted: bool


async def preview_mapping(
    ctx: WorkspaceContext, reader: VaultReader, mapping: VaultMapping
) -> list[PreviewRow]:
    """A dry run of the mapping over the vault's notes (ObsidianSetup's preview, computed
    by the worker): nothing is written."""
    await reader.refresh()
    templates = templates_folder(await _config_text(reader))
    listed = await reader.list_files(lambda path: is_excluded(path, mapping, templates))
    async with tenant_session(ctx) as s:
        known = await _known_projects(s)
    scan = _Scan(listing={item.path: item for item in listed}, docs={})
    await _read_notes(reader, scan, mapping, known)
    return [
        PreviewRow(
            path=path,
            project_id=scan.notes[path].project_id if path in scan.notes else None,
            ignored=path not in scan.notes,
            untrusted=note_trust(path, mapping) == "untrusted",
        )
        for path in sorted(scan.listing)
        if is_note_path(path) and path not in scan.kept
    ]


# --- Reading ------------------------------------------------------------------------------


async def _config_text(reader: VaultReader) -> str | None:
    """`.obsidian/templates.json`, the one file under `.obsidian/` the sync opens."""
    try:
        return (await reader.read(TEMPLATES_CONFIG)).decode("utf-8", errors="replace")
    except StorageError:
        return None


async def _known_projects(s: AsyncSession) -> dict[str, UUID]:
    """Live, active projects by `project_slug` of their name; a slug two projects share
    names neither."""
    names = await projects.project_names(s, await projects.active_project_ids(s))
    slugs = Counter(project_slug(name) for name in names.values())
    return {
        project_slug(name): pid for pid, name in names.items() if slugs[project_slug(name)] == 1
    }


async def _connection_docs(s: AsyncSession, connection_id: UUID) -> dict[str, dict[str, Any]]:
    rows = (
        (await s.execute(select(_documents).where(_documents.c.connection_id == connection_id)))
        .mappings()
        .all()
    )
    return {row["path"]: dict(row) for row in rows if row["path"] is not None}


async def _read_notes(
    reader: VaultReader, scan: _Scan, mapping: VaultMapping, known: Mapping[str, UUID]
) -> None:
    for path, stat in scan.listing.items():
        if not is_note_path(path):
            continue
        try:
            data = await reader.read(path)
        except NotFound:
            continue  # gone since the listing: its Document goes to the trash
        except StorageError:
            scan.kept.add(path)  # too large or refused: left as it was
            scan.report.skipped.append(path)
            continue
        parsed = parse_note(path, data.decode("utf-8", errors="replace"))
        slug = map_note_to_project(parsed, mapping, set(known))
        if slug == "ignore":
            continue
        scan.notes[path] = _Note(
            stat=stat,
            parsed=parsed,
            project_id=known[slug] if slug is not None else None,
            untrusted=note_trust(path, mapping) == "untrusted",
        )


# --- Notes --------------------------------------------------------------------------------


def _tags(tags: Iterable[str]) -> list[str]:
    """The note's tags as stored: sorted, within the knowledge base's limits (extra or
    over-long tags are left off rather than refusing the note)."""
    return sorted(tag for tag in tags if 0 < len(tag) <= MAX_TAG_CHARS)[:MAX_TAGS]


def _fields(note: _Note) -> dict[str, Any]:
    return {
        "title": note.parsed.title,
        "tags": _tags(note.parsed.tags),
        "project_id": note.project_id,
    }


async def _apply_notes(s: AsyncSession, scan: _Scan, connection_id: UUID, now: datetime) -> None:
    renames = _renames(scan)
    for path, note in sorted(scan.notes.items()):
        doc = scan.docs.get(path)
        if doc is None and path in renames:
            doc = renames[path]
            del scan.docs[doc["path"]]
            await _move(s, doc, path, note, now)
            scan.docs[path] = doc
            scan.report.renamed += 1
        elif doc is None:
            scan.docs[path] = await _create_note(s, connection_id, note, now)
            scan.report.created += 1
            continue
        if doc["deleted_at"] is not None:
            await api.restore(s, doc["id"])
            doc["deleted_at"] = None
            scan.report.restored += 1
        if await _update_note(s, doc, note, now):
            scan.report.changed += 1


def _renames(scan: _Scan) -> dict[str, dict[str, Any]]:
    """New note paths that take over a gone note's Document: the same file hash, the old
    path no longer listed, the new path with no Document of its own (live or trashed)."""
    gone: dict[str, list[dict[str, Any]]] = {}
    for path, doc in sorted(scan.docs.items()):
        if (
            doc["kind"] == "text"
            and doc["deleted_at"] is None
            and path not in scan.notes
            and path not in scan.kept
            and doc["source_revision"]
        ):
            gone.setdefault(doc["source_revision"], []).append(doc)
    renames: dict[str, dict[str, Any]] = {}
    for path, note in sorted(scan.notes.items()):
        candidates = gone.get(note.stat.etag)
        if path not in scan.docs and candidates:
            renames[path] = candidates.pop(0)
    return renames


async def _create_note(
    s: AsyncSession, connection_id: UUID, note: _Note, now: datetime
) -> dict[str, Any]:
    body = note.parsed.body
    row = (
        (
            await s.execute(
                insert(_documents)
                .values(
                    **_fields(note),
                    kind="text",
                    trust="untrusted" if note.untrusted else "trusted",
                    tainted=note.untrusted,
                    path=note.parsed.path,
                    connection_id=connection_id,
                    external_id=note.parsed.path,
                    fetched_at=now,
                    source=SOURCE,
                    source_revision=note.stat.etag,
                    content_hash=hashlib.sha256(body.encode()).digest(),
                    status="ready",
                )
                .returning(*_documents.c)
            )
        )
        .mappings()
        .one()
    )
    await api.write_synced_text(s, row, body, expected_version=row["version"], added=True)
    return await _reload(s, row["id"])


async def _move(
    s: AsyncSession, doc: dict[str, Any], path: str, note: _Note, now: datetime
) -> None:
    """A rename: the Document follows the file to its new path; no new version."""
    values = {"path": path, "external_id": path, "fetched_at": now, **_fields(note)}
    await s.execute(update(_documents).where(_documents.c.id == doc["id"]).values(**values))
    doc.update(await _reload(s, doc["id"]))


async def _update_note(s: AsyncSession, doc: dict[str, Any], note: _Note, now: datetime) -> bool:
    """The Document brought up to the note: its fields, trust lowered for a clipping, and
    a new version for a new body. False when nothing differed (nothing written)."""
    values = {k: v for k, v in _fields(note).items() if doc[k] != v}
    if doc["source_revision"] != note.stat.etag:
        values["source_revision"] = note.stat.etag
    if note.untrusted and (doc["trust"] != "untrusted" or not doc["tainted"]):
        values.update(trust="untrusted", tainted=True)
    body_changed = doc["body_md"] != note.parsed.body
    if not values and not body_changed:
        return False
    if values:
        values["fetched_at"] = now
        await s.execute(update(_documents).where(_documents.c.id == doc["id"]).values(**values))
    if body_changed:
        row = await _reload(s, doc["id"])
        await api.write_synced_text(
            s, row, note.parsed.body, expected_version=row["version"], added=False
        )
    doc.update(await _reload(s, doc["id"]))
    return True


async def _reload(s: AsyncSession, document_id: UUID) -> dict[str, Any]:
    row = (
        (await s.execute(select(_documents).where(_documents.c.id == document_id))).mappings().one()
    )
    return dict(row)


# --- Attachments --------------------------------------------------------------------------


def _embedded(scan: _Scan) -> dict[str, list[_Note]]:
    """Each listed non-note file a synced note embeds, with the notes embedding it (in
    path order)."""
    found: dict[str, list[_Note]] = {}
    for path, note in sorted(scan.notes.items()):
        for link in note.parsed.embeds:
            target = resolve_link(link.target, path, scan.index)
            if target is not None and not is_note_path(target):
                found.setdefault(target, []).append(note)
    return found


async def _apply_attachments(
    s: AsyncSession, reader: VaultReader, scan: _Scan, connection_id: UUID, now: datetime
) -> None:
    for path, notes in sorted(_embedded(scan).items()):
        stat = scan.listing[path]
        project_id = notes[0].project_id
        tainted = any(note.untrusted for note in notes)
        doc = scan.docs.get(path)
        if doc is not None and doc["deleted_at"] is not None:
            await api.restore(s, doc["id"])
            doc["deleted_at"] = None
            scan.report.restored += 1
        if doc is not None and doc["source_revision"] == stat.etag:
            values: dict[str, Any] = {}
            if doc["project_id"] != project_id:
                values["project_id"] = project_id
            if tainted and not doc["tainted"]:
                values["tainted"] = True
            if values:
                await s.execute(
                    update(_documents).where(_documents.c.id == doc["id"]).values(**values)
                )
            if _extraction_lost(doc, now):
                await _request_again(reader, scan, doc["current_version_id"], path)
            scan.kept.add(path)
            continue
        try:
            data = await reader.read(path)
        except StorageError:
            scan.report.skipped.append(path)
            if doc is not None:
                scan.kept.add(path)
            continue
        if doc is None:
            doc = await _create_attachment(
                s, connection_id, path, stat, project_id=project_id, tainted=tainted, now=now
            )
            scan.docs[path] = doc
        else:
            await s.execute(
                update(_documents)
                .where(_documents.c.id == doc["id"])
                .values(
                    project_id=project_id,
                    tainted=doc["tainted"] or tainted,
                    status="pending_scan",
                    content_hash=hashlib.sha256(data).digest(),
                    source_revision=stat.etag,
                    fetched_at=now,
                )
            )
        version_id = await api.add_version(
            s, doc["id"], data, None, source_name=posixpath.basename(path)
        )
        await asyncio.to_thread(_spool, version_id, data)
        scan.extractions.append((version_id, path))
        scan.kept.add(path)
        scan.report.attachments += 1


async def _create_attachment(  # the file and where it goes
    s: AsyncSession,
    connection_id: UUID,
    path: str,
    stat: FileStat,
    *,
    project_id: UUID | None,
    tainted: bool,
    now: datetime,
) -> dict[str, Any]:
    """An untrusted file Document, `pending_scan` until P1-16's pipeline releases it."""
    row = (
        (
            await s.execute(
                insert(_documents)
                .values(
                    project_id=project_id,
                    title=posixpath.basename(path),
                    kind="file",
                    trust="untrusted",
                    tainted=tainted,
                    path=path,
                    connection_id=connection_id,
                    external_id=path,
                    fetched_at=now,
                    source=SOURCE,
                    source_revision=stat.etag,
                    content_hash=bytes.fromhex(stat.etag),
                    status="pending_scan",
                )
                .returning(*_documents.c)
            )
        )
        .mappings()
        .one()
    )
    api.announce(s, project_id, row["id"])
    return dict(row)


def _extraction_lost(doc: Mapping[str, Any], now: datetime) -> bool:
    """Still `pending_scan` a while after its extraction was requested (`fetched_at`)."""
    return (
        doc["status"] == "pending_scan"
        and doc["current_version_id"] is not None
        and doc["fetched_at"] is not None
        and now - doc["fetched_at"] >= EXTRACTION_RETRY_AFTER
    )


async def _request_again(reader: VaultReader, scan: _Scan, version_id: UUID, path: str) -> None:
    """An unchanged attachment still waiting for extraction (its request was lost, say to a
    crash between the commit and the enqueue) is requested again; `extract:<version_id>`
    makes a repeat of a live request a no-op. Its spooled bytes are written again when they
    are gone."""
    if not await asyncio.to_thread(pipeline.spool_file(version_id).is_file):
        try:
            data = await reader.read(path)
        except StorageError:
            scan.report.skipped.append(path)
            return
        await asyncio.to_thread(_spool, version_id, data)
    scan.extractions.append((version_id, path))


def _spool(version_id: UUID, data: bytes) -> None:
    target = pipeline.spool_file(version_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


# --- The trash ----------------------------------------------------------------------------


async def _trash_missing(s: AsyncSession, scan: _Scan) -> None:
    """Live Documents whose note is gone (or now ignored) and attachments nothing
    embeds any more."""
    for path, doc in sorted(scan.docs.items()):
        if doc["deleted_at"] is not None or path in scan.kept:
            continue
        if doc["kind"] == "text" and path in scan.notes:
            continue
        await api.trash_document(s, doc["id"])
        doc["deleted_at"] = True
        scan.report.trashed += 1


# --- Links --------------------------------------------------------------------------------

_LinkRow = tuple[str, str, str | None, str | None, UUID | None]  # kind, target, heading, ...


def _wanted(scan: _Scan, note: _Note) -> list[_LinkRow]:
    return [
        _row(scan, note.parsed.path, link) for link in (*note.parsed.links, *note.parsed.embeds)
    ]


def _row(scan: _Scan, from_path: str, link: Link) -> _LinkRow:
    target = resolve_link(link.target, from_path, scan.index)
    doc = scan.docs.get(target) if target is not None else None
    live = doc is not None and doc["deleted_at"] is None
    return (
        "embed" if link.embed else "link",
        link.target,
        link.heading,
        link.block,
        doc["id"] if live and doc is not None else None,
    )


async def _write_links(s: AsyncSession, scan: _Scan) -> None:
    """Each synced note's `document_links`, replaced only when they differ."""
    note_ids = {scan.docs[path]["id"]: note for path, note in scan.notes.items()}
    if not note_ids:
        return
    existing: dict[UUID, Counter[_LinkRow]] = {doc_id: Counter() for doc_id in note_ids}
    rows = await s.execute(
        select(
            _links.c.from_document_id,
            _links.c.kind,
            _links.c.to_target,
            _links.c.heading,
            _links.c.block,
            _links.c.to_document_id,
        ).where(_links.c.from_document_id.in_(list(note_ids)))
    )
    for row in rows:
        existing[row.from_document_id][
            (row.kind, row.to_target, row.heading, row.block, row.to_document_id)
        ] += 1
    for doc_id, note in note_ids.items():
        wanted = _wanted(scan, note)
        if Counter(wanted) == existing[doc_id]:
            continue
        await s.execute(delete(_links).where(_links.c.from_document_id == doc_id))
        if wanted:
            await s.execute(
                insert(_links),
                [
                    {
                        "from_document_id": doc_id,
                        "kind": kind,
                        "to_target": target,
                        "heading": heading,
                        "block": block,
                        "to_document_id": to_id,
                    }
                    for kind, target, heading, block, to_id in wanted
                ],
            )
