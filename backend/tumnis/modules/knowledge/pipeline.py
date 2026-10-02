"""The extraction pipeline's step bodies (P1-16, SEC-10, FR-15.2, ADR-0007).

`workflows.extract_document` is the DBOS workflow: it calls each function named in `STEPS`
as `pipeline.<step>(...)`, looked up at call time so a test can wrap a step (the step log)
and so nothing else needs to know how a step works. Arguments and results are small JSON
values (ids as strings, a `Ref` for the file); large outputs (the converted document, its
Markdown, the chunks) go to `extraction_artifacts`, never through DBOS.

Files: an upload waits in `<spool>/<version_id>` (the api wrote it); step 1 copies it to
`<scratch>/<version_id>/<name>` (a file found in a project folder is read from its location
instead), and every later step works on that copy. The copy keeps the file's name, since
Docling reads the format from the extension; a step that finds it gone brings it back from
the spool or from where the file was placed. An object of an S3 linked source (P3-13,
`source = "linked"`) waits in the spool too, but is never placed: it lives in its bucket,
and a lost spool copy is read again from there (`register_linked_reader`).

The scanner, the extractor and the vision model come from `use()` (tests) or, in a real
deployment, the settings given to `configure()`: clamd's address, Docling with the chunk
tokenizer, and the vision model when `vision_base_url` and `vision_model` are set (none
otherwise: low-confidence pages keep Docling's chunks); in fakes mode
(`TUMNIS_ADAPTERS=fake`) the fakes.
"""

import asyncio
import contextlib
import gzip
import hashlib
import hmac
import json
import logging
import shutil
import zipfile
from collections.abc import AsyncGenerator, Callable
from itertools import count
from pathlib import Path, PurePosixPath
from typing import Any, Final, Literal, TypedDict, cast
from uuid import UUID

from tumnis.core import audit
from tumnis.core.adapters.registry import current_mode, resolve
from tumnis.core.clock import SystemClock
from tumnis.core.net import NetPolicy
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.knowledge import api
from tumnis.modules.knowledge import store as records
from tumnis.modules.knowledge.adapters.clamav import ClamAV
from tumnis.modules.knowledge.adapters.docling import DoclingExtractor
from tumnis.modules.knowledge.adapters.fake import FakeDocling, FakeVision
from tumnis.modules.knowledge.adapters.port import ChunkRow, Extractor, Scanner, Vision
from tumnis.modules.knowledge.rules import (
    MAX_UPLOAD_BYTES,
    Refusal,
    classify_type,
    low_confidence_pages,
    numbered_name,
    upload_file_name,
)
from tumnis.modules.knowledge.storage import FileStat, PreconditionFailed, StorageBackend
from tumnis.settings import KnowledgeSettings

log = logging.getLogger(__name__)

STEPS: Final = (
    "read",
    "scan",
    "quarantine",
    "sniff",
    "place",
    "convert",
    "vlm",
    "store",
    "chunk",
    "index",
    "emit",
    "fail",
)
Source = Literal["spool", "storage", "linked"]
LinkedReader = Callable[[WorkspaceContext, UUID], AsyncGenerator[bytes]]


class LinkedObjectChangedError(FileNotFoundError):
    """A linked version's object, read again from its bucket, no longer holds that
    version's bytes: step 1 fails rather than give the version another file's hash (the
    next sync takes the new bytes in as a new version)."""


SNIFF_BYTES: Final = 8 * 1024
READ_BYTES: Final = 1024 * 1024
_OOXML: Final = "application/vnd.openxmlformats-officedocument."
_OOXML_DIRS: Final = (
    ("word/", _OOXML + "wordprocessingml.document"),
    ("xl/", _OOXML + "spreadsheetml.sheet"),
    ("ppt/", _OOXML + "presentationml.presentation"),
)


class Ref(TypedDict):
    """The file as step 1 read it: its content hash and size, and the name it arrived
    under (the upload's, or the folder file's)."""

    sha256: str
    size: int
    name: str
    source: Source


_settings = KnowledgeSettings()
_net = NetPolicy(mode="self-hosted")
_use: dict[str, Any] = {"scanner": None, "extractor": None, "vision": None}
_defaults: dict[str, Any] = {}  # built on first use, kept for the process


def configure(settings: KnowledgeSettings, *, net: NetPolicy | None = None) -> KnowledgeSettings:
    """Set the spool and scratch folders, clamd's address and the SSRF policy the worker
    reaches storage under; returns the previous settings."""
    global _settings, _net  # noqa: PLW0603  # process-wide, like the adapter registry
    previous = _settings
    _settings = settings
    _defaults.clear()
    if net is not None:
        _net = net
    return previous


def use(**parts: Any) -> dict[str, Any]:
    """Swap the `scanner`, `extractor` and `vision` the steps use (tests; None puts the
    default back); returns the previous values of the parts given, ready for `use(**...)`."""
    previous = {name: _use[name] for name in parts}
    _use.update(parts)
    return previous


def _scanner() -> Scanner:
    if _use["scanner"] is not None:
        return cast("Scanner", _use["scanner"])
    if "scanner" not in _defaults:
        if current_mode() == "fake":
            _defaults["scanner"] = resolve("knowledge.clamav", "fake")
        else:
            _defaults["scanner"] = ClamAV(
                _settings.clamd_host, _settings.clamd_port, clock=SystemClock()
            )
    return cast("Scanner", _defaults["scanner"])


def _extractor() -> Extractor:
    if _use["extractor"] is not None:
        return cast("Extractor", _use["extractor"])
    if "extractor" not in _defaults:
        _defaults["extractor"] = (
            FakeDocling()
            if current_mode() == "fake"
            else DoclingExtractor(chunk_tokenizer=_settings.chunk_tokenizer)
        )
    return cast("Extractor", _defaults["extractor"])


def _vision() -> Vision | None:
    if _use["vision"] is not None:
        return cast("Vision", _use["vision"])
    if "vision" not in _defaults:
        # A real deployment reads low-confidence pages only when a vision model is set.
        if current_mode() == "fake":
            _defaults["vision"] = FakeVision()
        elif _settings.vision_base_url and _settings.vision_model:
            _defaults["vision"] = resolve(
                "knowledge.vision",
                "real",
                base_url=_settings.vision_base_url,
                model=_settings.vision_model,
                clock=SystemClock(),
                net_policy=_net,
            )
        else:
            _defaults["vision"] = None
    return cast("Vision | None", _defaults["vision"])


# --- Files ---------------------------------------------------------------------------------


def _ctx(workspace_id: str) -> WorkspaceContext:
    return WorkspaceContext(UUID(workspace_id), SYSTEM_ACTOR)


def _spool_file(version_id: str) -> Path:
    return Path(_settings.spool_dir) / version_id


def spool_path(version_id: UUID) -> Path:
    """Where a version's file waits for step 1 (`<spool>/<version_id>`)."""
    return _spool_file(str(version_id))


_linked: list[LinkedReader | None] = [None]


def register_linked_reader(fn: LinkedReader) -> None:
    """How a linked source's version is read again when its spool copy is gone (P3-13:
    `s3_sync` reads the object from its bucket)."""
    _linked[0] = fn


def _scratch_dir(version_id: str) -> Path:
    return Path(_settings.scratch_dir) / version_id


def _scratch_file(version_id: str, name: str) -> Path:
    return _scratch_dir(version_id) / upload_file_name(name)


async def _file_chunks(path: Path) -> AsyncGenerator[bytes]:
    """`path` in 1 MiB reads, each in a thread."""
    handle = await asyncio.to_thread(path.open, "rb")
    try:
        while data := await asyncio.to_thread(handle.read, READ_BYTES):
            yield data
    finally:
        await asyncio.to_thread(handle.close)


async def _copy(chunks: AsyncGenerator[bytes], dest: Path) -> tuple[str, int]:
    """Write `chunks` to `dest` (its folder made), hashing them; stops one byte past the
    upload limit so an oversize file is measured, not copied whole. The bytes go to a
    `.part` file beside `dest` that replaces it only once the copy is whole, so a failed
    read (a linked object that changed, a dropped stream) never leaves a partial `dest`
    for a later step to take as the scratch copy."""
    await asyncio.to_thread(dest.parent.mkdir, parents=True, exist_ok=True)
    part = dest.with_name(f".{dest.name}.part")
    digest = hashlib.sha256()
    size = 0
    handle = await asyncio.to_thread(part.open, "wb")
    try:
        try:
            async with contextlib.aclosing(chunks):
                async for received in chunks:
                    chunk = received[: MAX_UPLOAD_BYTES + 1 - size]
                    size += len(chunk)
                    digest.update(chunk)
                    await asyncio.to_thread(handle.write, chunk)
                    if size > MAX_UPLOAD_BYTES:
                        break
        finally:
            await asyncio.to_thread(handle.close)
        await asyncio.to_thread(part.replace, dest)
    except BaseException:
        await asyncio.to_thread(part.unlink, missing_ok=True)
        raise
    return digest.hexdigest(), size


async def _fetch(
    ctx: WorkspaceContext, version_id: str, source: Source, dest: Path
) -> tuple[str, int]:
    """Copy the version's file to `dest`: the spool file when there is one, else the file
    on its location (a folder file, or an upload that was already placed)."""
    spool = _spool_file(version_id)
    if source in ("spool", "linked") and spool.is_file():
        return await _copy(_file_chunks(spool), dest)
    if source == "linked":
        reader = _linked[0]
        if reader is None:
            raise FileNotFoundError(f"no spool copy of {version_id} and no linked reader")
        return await _copy(reader(ctx, UUID(version_id)), dest)
    async with tenant_session(ctx) as s:
        info = await records.version_info(s, UUID(version_id))
        if info.path is None or info.location_id is None:
            raise FileNotFoundError(f"no file for version {version_id}: no spool copy, not placed")
        full = await api.storage_path(s, info.project_id, info.path)
        async with api.open_backend(s, info.location_id, net=_net) as backend:
            return await _copy(cast("AsyncGenerator[bytes]", backend.read(full)), dest)


async def _scratch(workspace_id: str, version_id: str, ref: Ref) -> Path:
    """The scratch copy, brought back when a restart or a cleanup lost it."""
    path = _scratch_file(version_id, ref["name"])
    if not path.is_file():
        await _fetch(_ctx(workspace_id), version_id, ref["source"], path)
    return path


async def _remove(version_id: str, *, spool: bool = True) -> None:
    def clean() -> None:
        shutil.rmtree(_scratch_dir(version_id), ignore_errors=True)
        if spool:
            _spool_file(version_id).unlink(missing_ok=True)

    await asyncio.to_thread(clean)


# --- Steps ---------------------------------------------------------------------------------


async def read(workspace_id: str, version_id: str, source: Source) -> Ref:
    """1. Stream the file to `<scratch>/<version_id>/<name>` hashing it; the upload's own
    spool file, or the document's file on its location. The hash and size go on the rows."""
    ctx, vid = _ctx(workspace_id), UUID(version_id)
    async with tenant_session(ctx) as s:
        info = await records.version_info(s, vid)
    name = info.source_name or PurePosixPath(info.path or info.title).name
    digest, size = await _fetch(ctx, version_id, source, _scratch_file(version_id, name))
    async with tenant_session(ctx) as s:
        await records.set_content(s, vid, digest=bytes.fromhex(digest), size=size)
    return {"sha256": digest, "size": size, "name": name, "source": source}


async def scan(workspace_id: str, version_id: str, ref: Ref) -> dict[str, Any]:
    """2. Stream the scratch copy through clamd: {"infected": bool, "signature": str|None}.
    A clean scan moves the version to `extracting`."""
    path = await _scratch(workspace_id, version_id, ref)
    result = await _scanner().scan(_file_chunks(path))
    if not result.infected:
        async with tenant_session(_ctx(workspace_id)) as s:
            await records.set_status(s, UUID(version_id), "extracting")
    return {"infected": result.infected, "signature": result.signature}


async def quarantine(workspace_id: str, version_id: str, signature: str, source: Source) -> None:
    """2b. Version and document `quarantined`, one `upload.quarantined` audit row, the spool
    and scratch copies removed. A file in a project folder is never touched."""
    vid = UUID(version_id)
    async with tenant_session(_ctx(workspace_id)) as s:
        info = await records.version_info(s, vid)
        if info.status != "quarantined":  # a retry after the commit writes nothing more
            await records.set_status(s, vid, "quarantined", signature)
            await audit.record(
                s,
                "upload.quarantined",
                target=("documents", info.document_id),
                details={"signature": signature, "source": source},
                occurred_at=SystemClock().now(),
            )
    await _remove(version_id)


def _sniffed_mime(path: Path) -> str:
    import magic  # noqa: PLC0415  # libmagic is loaded when the first file is sniffed

    with path.open("rb") as handle:
        head = handle.read(SNIFF_BYTES)
    mime: str = magic.from_buffer(head, mime=True)
    if mime == "application/zip":
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
        except zipfile.BadZipFile:
            return mime
        if "[Content_Types].xml" in names:
            for prefix, ooxml in _OOXML_DIRS:
                if any(name.startswith(prefix) for name in names):
                    return ooxml
    return mime


async def sniff(workspace_id: str, version_id: str, ref: Ref) -> dict[str, Any]:
    """3. libmagic on the first 8 KiB, the size limit and `classify_type`:
    {"kind": str|None, "mime": str, "refusal": str|None}."""
    path = await _scratch(workspace_id, version_id, ref)
    mime = await asyncio.to_thread(_sniffed_mime, path)
    verdict: Refusal | str
    if ref["size"] > MAX_UPLOAD_BYTES:
        verdict = Refusal("too_large")
    else:
        verdict = classify_type(mime, ref["name"])
    kind = None if isinstance(verdict, Refusal) else verdict
    async with tenant_session(_ctx(workspace_id)) as s:
        await records.set_sniffed(s, UUID(version_id), mime=mime, kind=kind)
    return {
        "kind": kind,
        "mime": mime,
        "refusal": verdict.code if isinstance(verdict, Refusal) else None,
    }


async def _same_file(backend: StorageBackend, path: str, sha256: str) -> bool:
    digest = hashlib.sha256()
    async for chunk in backend.read(path):
        digest.update(chunk)
    return hmac.compare_digest(digest.hexdigest(), sha256)


async def place(workspace_id: str, version_id: str, ref: Ref) -> str:
    """3b. (uploads) Write the scratch copy to `uploads/<name>` in the document's folder,
    create-only (a taken name gets a number; a file already there with the same content is
    this upload, placed by an earlier attempt); returns the path relative to the folder.
    The file is recorded in `folder_files` as Tumnis's own, so the folder sync does not
    take it for an outside file (#99)."""
    ctx, vid = _ctx(workspace_id), UUID(version_id)
    path = await _scratch(workspace_id, version_id, ref)
    name = upload_file_name(ref["name"])
    async with tenant_session(ctx) as s:
        info = await records.version_info(s, vid)
        if info.project_id is not None:  # a move's switch waits for this write, or it for it
            await api.hold_folder_for_write(s, info.project_id)
            info = await records.version_info(s, vid)
        if info.location_id is None:
            raise RuntimeError(f"document {info.document_id} has no location")
        root = await api.storage_path(s, info.project_id, "")
        uploads = await api.upload_dir(s, info.project_id)
        found: tuple[str, FileStat] | None = None
        async with api.open_backend(s, info.location_id, net=_net) as backend:
            for attempt in count(1):
                rel = f"{uploads}/{numbered_name(name, attempt)}"
                try:
                    found = (rel, await backend.write(f"{root}{rel}", _file_chunks(path), None))
                except PreconditionFailed as taken:
                    if taken.current is not None and await _same_file(
                        backend, f"{root}{rel}", ref["sha256"]
                    ):
                        found = (rel, taken.current)
                        break
                    continue
                break
        assert found is not None  # noqa: S101  # the loop only leaves by placing
        placed, stat = found
        await records.set_path(s, info.document_id, placed)
        await api.record_placed(
            s, info.location_id, stat, document_id=info.document_id, sha256=ref["sha256"]
        )
    await asyncio.to_thread(_spool_file(version_id).unlink, missing_ok=True)
    return placed


async def convert(workspace_id: str, version_id: str, ref: Ref, kind: str) -> dict[str, Any]:
    """4. The extractor converts the scratch copy; the document and its Markdown are stored
    as artifacts: {"low_pages": [int, ...]}."""
    path = await _scratch(workspace_id, version_id, ref)
    extractor = _extractor()
    conversion = await asyncio.to_thread(extractor.convert, path, cast("Any", kind))
    vid = UUID(version_id)
    async with tenant_session(_ctx(workspace_id)) as s:
        await records.put_artifact(s, vid, "docling", conversion.doc_json)
        await records.put_artifact(s, vid, "markdown", conversion.markdown.encode())
    low = low_confidence_pages(conversion.grades, conversion.text_items) if kind == "pdf" else []
    return {"low_pages": low}


async def vlm(workspace_id: str, version_id: str, ref: Ref, low_pages: list[int]) -> None:
    """5. The vision model reads each low-confidence page; its Markdown is stored. Nothing
    to do without low pages or a vision model."""
    vision = _vision()
    if not low_pages or vision is None:
        return
    path = await _scratch(workspace_id, version_id, ref)
    extractor = _extractor()
    pages: dict[str, str] = {}
    for page in low_pages:
        image = await asyncio.to_thread(extractor.page_image, path, page)
        pages[str(page)] = await vision.page_markdown(image, page=page)
    async with tenant_session(_ctx(workspace_id)) as s:
        await records.put_artifact(s, UUID(version_id), "vlm_pages", json.dumps(pages).encode())


async def _artifact(ctx: WorkspaceContext, version_id: str, stage: str) -> bytes | None:
    async with tenant_session(ctx) as s:
        return await records.get_artifact(s, UUID(version_id), stage)


async def store(workspace_id: str, version_id: str) -> None:
    """6. The compressed document and its Markdown export go on the version."""
    ctx = _ctx(workspace_id)
    doc = await _artifact(ctx, version_id, "docling")
    markdown = await _artifact(ctx, version_id, "markdown")
    if doc is None or markdown is None:
        raise RuntimeError(f"version {version_id} was not converted")
    packed = await asyncio.to_thread(gzip.compress, doc)
    async with tenant_session(ctx) as s:
        await records.set_document(
            s, UUID(version_id), docling_json=packed, body_md=markdown.decode()
        )


def merge_vision_pages(
    extractor: Extractor, chunks: list[ChunkRow], pages: dict[int, str]
) -> list[ChunkRow]:
    """`chunks` with each vision page's Markdown chunked in place of the standard chunks
    that lie exactly on that page; a page Docling found no text on gets its chunks where
    its number puts them. Ordinals run 0..n-1 in the new order."""
    merged = [c for c in chunks if not (c.page_from == c.page_to and c.page_from in pages)]
    for page in sorted(pages):
        if not pages[page].strip():
            continue
        new = [
            c.model_copy(update={"page_from": page, "page_to": page, "extractor": "vlm"})
            for c in extractor.chunk_markdown(pages[page])
        ]
        at = next(
            (i for i, c in enumerate(merged) if c.page_from is not None and c.page_from > page),
            len(merged),
        )
        merged[at:at] = new
    return [c.model_copy(update={"ordinal": i}) for i, c in enumerate(merged)]


async def chunk(workspace_id: str, version_id: str) -> int:
    """7. Chunk the document (vision pages replace the standard chunks of their page);
    the chunks are stored as an artifact; returns how many."""
    ctx = _ctx(workspace_id)
    doc = await _artifact(ctx, version_id, "docling")
    if doc is None:
        raise RuntimeError(f"version {version_id} was not converted")
    extractor = _extractor()
    chunks = await asyncio.to_thread(extractor.chunk, doc)
    raw = await _artifact(ctx, version_id, "vlm_pages")
    if raw is not None:
        pages = {int(page): text for page, text in json.loads(raw).items()}
        chunks = await asyncio.to_thread(merge_vision_pages, extractor, chunks, pages)
    else:
        chunks = [c.model_copy(update={"ordinal": i}) for i, c in enumerate(chunks)]
    payload = json.dumps([c.model_dump() for c in chunks]).encode()
    async with tenant_session(ctx) as s:
        await records.put_artifact(s, UUID(version_id), "chunks", payload)
    return len(chunks)


async def index(workspace_id: str, version_id: str) -> int:
    """8. The chunks become `chunks` rows, once (a rerun replaces them), and get their
    vectors from the Embeddings slot (P3-10): a failed embedding is logged and skipped,
    never a failed extraction (full-text search still finds the chunks, and a later
    re-embed fills the gap)."""
    ctx, vid = _ctx(workspace_id), UUID(version_id)
    raw = await _artifact(ctx, version_id, "chunks")
    if raw is None:
        raise RuntimeError(f"version {version_id} was not chunked")
    rows = [ChunkRow.model_validate(row) for row in json.loads(raw)]
    async with tenant_session(ctx) as s:
        info = await records.version_info(s, vid)
        await records.replace_chunks(s, info.document_id, vid, rows)
    try:
        await api.embed_document(ctx, info.document_id, vid)
    except Exception:  # the embedder is an improvement, never a gate
        log.warning("embedding version %s failed; its chunks stay full-text only", version_id)
    return len(rows)


async def emit(workspace_id: str, version_id: str) -> str:
    """9. Version and document `ready`, the version current, and `document.added` (or
    `document.changed` for a document that had a current version) in the same transaction."""
    async with tenant_session(_ctx(workspace_id)) as s:
        event = await records.mark_ready(s, UUID(version_id))
    await _remove(version_id)
    return event


async def fail(workspace_id: str, version_id: str, code: str) -> None:
    """A refused or failed file: version and document `failed` with the code as the reason;
    the spool and scratch copies removed."""
    async with tenant_session(_ctx(workspace_id)) as s:
        await records.set_status(s, UUID(version_id), "failed", code)
    await _remove(version_id)
