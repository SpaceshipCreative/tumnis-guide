"""`FakeStorage`: the in-memory tree behind every storage adapter in fakes mode (P1-14).

It keeps the port's promises exactly (create-only and etag-matched writes, no-clobber
moves, safe paths, paged listings), so the shared contract suite runs against it too.
Etags are sha256 hex, as on a server path. Scripting: `script(health=...)` sets what
`health()` answers; `calls` records every (operation, path).
"""

import hashlib
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from tumnis.core.clock import Clock, SystemClock
from tumnis.modules.knowledge.adapters.port import ChunkRow, Conversion, ScanResult
from tumnis.modules.knowledge.rules import DocKind, etag_equal, safe_rel_path
from tumnis.modules.knowledge.storage import (
    LIST_PAGE_SIZE,
    FileStat,
    Health,
    NotFound,
    Page,
    PreconditionFailed,
    safe_prefix,
    spool,
)

CHUNK = 64 * 1024


@dataclass(frozen=True)
class _Entry:
    data: bytes
    mtime: datetime
    etag: str


class FakeStorage:
    def __init__(self, clock: Clock | None = None, *, page_size: int = LIST_PAGE_SIZE) -> None:
        self._clock = clock or SystemClock()
        self._page_size = page_size
        self._files: dict[str, _Entry] = {}
        self._health = Health.ok()
        self.calls: list[tuple[str, str]] = []

    def script(self, *, health: Health | None = None) -> None:
        if health is not None:
            self._health = health

    def _stat(self, path: str) -> FileStat | None:
        entry = self._files.get(path)
        if entry is None:
            return None
        return FileStat(path=path, size=len(entry.data), mtime=entry.mtime, etag=entry.etag)

    async def stat(self, path: str) -> FileStat | None:
        path = safe_rel_path(path)
        self.calls.append(("stat", path))
        return self._stat(path)

    async def list(self, prefix: str, cursor: str | None) -> Page[FileStat]:
        prefix = safe_prefix(prefix)
        self.calls.append(("list", prefix))
        keys = sorted(k for k in self._files if k.startswith(prefix) and (not cursor or k > cursor))
        page = keys[: self._page_size]
        items = [st for k in page if (st := self._stat(k)) is not None]
        more = len(keys) > len(page)
        return Page[FileStat](items=items, next_cursor=page[-1] if more and page else None)

    async def read(self, path: str) -> AsyncIterator[bytes]:
        path = safe_rel_path(path)
        self.calls.append(("read", path))
        entry = self._files.get(path)
        if entry is None:
            raise NotFound(path)
        for start in range(0, len(entry.data), CHUNK):
            yield entry.data[start : start + CHUNK]

    async def write(self, path: str, data: AsyncIterator[bytes], if_match: str | None) -> FileStat:
        path = safe_rel_path(path)
        self.calls.append(("write", path))
        body = await spool(data)
        current = self._stat(path)
        if if_match is None:
            if current is not None:
                raise PreconditionFailed(current)
        elif current is None or not etag_equal(current.etag, if_match):
            raise PreconditionFailed(current)
        entry = _Entry(body, self._clock.now(), hashlib.sha256(body).hexdigest())
        self._files[path] = entry
        return FileStat(path=path, size=len(body), mtime=entry.mtime, etag=entry.etag)

    async def move(self, src: str, dst: str) -> None:
        src, dst = safe_rel_path(src), safe_rel_path(dst)
        self.calls.append(("move", f"{src} -> {dst}"))
        if src not in self._files:
            raise NotFound(src)
        if (taken := self._stat(dst)) is not None:
            raise PreconditionFailed(taken)
        self._files[dst] = self._files.pop(src)

    async def delete(self, path: str) -> None:
        path = safe_rel_path(path)
        self.calls.append(("delete", path))
        self._files.pop(path, None)

    async def health(self) -> Health:
        return self._health


# --- Extraction pipeline fakes (P1-16) ----------------------------------------------------

EXTRACTION_FIXTURES = Path(__file__).resolve().parents[4] / "fixtures" / "extraction"
_PAGE_PNG = bytes.fromhex(  # a 1x1 PNG: what FakeDocling shows the vision fake
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cfc0f01f0005000201a5f645400000000049454e44ae426082"
)


# The EICAR anti-malware test string, in two halves so that no virus scanner on a dev
# machine quarantines this file (the same split as tests/_samples.py).
_EICAR = (r"X5O!P%@AP[4\PZX54(P^)7CC)7}$" + "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*").encode()
EICAR_SIGNATURE = "Win.Test.EICAR_HDB-1"  # clamd's name for it


class FakeClamAV:
    """Flags the EICAR test file and nothing else (A6). `calls` records the byte count of
    each scan."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    async def scan(self, stream: AsyncIterator[bytes]) -> ScanResult:
        size, tail, found = 0, b"", False
        async for chunk in stream:
            size += len(chunk)
            window = tail + chunk  # the string may straddle two chunks
            found = found or _EICAR in window
            tail = window[-(len(_EICAR) - 1) :]
        self.calls.append(size)
        return ScanResult(infected=found, signature=EICAR_SIGNATURE if found else None)


class FakeVision:
    """Per-page Markdown: `script(pages={n: markdown})`; a page not scripted answers a
    placeholder line. `calls` is the page numbers asked, in order."""

    def __init__(self) -> None:
        self._pages: dict[int, str] = {}
        self.calls: list[int] = []

    def script(self, *, pages: Mapping[int, str]) -> None:
        self._pages = dict(pages)

    async def page_markdown(self, image: bytes, *, page: int) -> str:
        self.calls.append(page)
        return self._pages.get(page, f"Page {page} read by the vision model.")

    async def health(self) -> Literal["ok", "degraded"]:
        return "ok"


def _one_chunk(text: str) -> ChunkRow:
    return ChunkRow(
        ordinal=0, text=text, context_text=text, heading_path=[], page_from=None, page_to=None
    )


class FakeDocling:
    """Stored conversions: a fixture file with a `<name>.conversion.json` beside it (its
    Markdown, per-page grades and text-item counts, and its chunks) converts to that, found
    by the file's sha256. Any other content is one chunk of its decoded text. `calls`
    records (file name, kind) for each conversion."""

    def __init__(self, fixtures: Path = EXTRACTION_FIXTURES) -> None:
        self.calls: list[tuple[str, str]] = []
        self._stored: dict[str, bytes] = {}
        for stored in sorted(fixtures.glob("*.conversion.json")):
            source = stored.with_name(stored.name.removesuffix(".conversion.json"))
            if source.is_file():
                self._stored[hashlib.sha256(source.read_bytes()).hexdigest()] = stored.read_bytes()

    def convert(self, path: Path, kind: DocKind) -> Conversion:
        self.calls.append((path.name, kind))
        data = path.read_bytes()
        doc = self._stored.get(hashlib.sha256(data).hexdigest())
        if doc is None:
            text = data.decode(errors="replace").replace("\x00", "")  # Postgres text holds no NUL
            doc = json.dumps({"markdown": text, "chunks": [_one_chunk(text).model_dump()]}).encode()
        stored = json.loads(doc)
        grades: dict[int, str | tuple[str, str]] = {
            int(page): grade if isinstance(grade, str) else (grade[0], grade[1])
            for page, grade in stored.get("grades", {}).items()
        }
        items = {int(page): int(count) for page, count in stored.get("text_items", {}).items()}
        return Conversion(doc, stored["markdown"], grades, items)

    def chunk(self, doc_json: bytes) -> list[ChunkRow]:
        return [ChunkRow.model_validate(row) for row in json.loads(doc_json)["chunks"]]

    def chunk_markdown(self, markdown: str) -> list[ChunkRow]:
        return [_one_chunk(markdown)]

    def page_image(self, path: Path, page: int) -> bytes:
        return _PAGE_PNG
