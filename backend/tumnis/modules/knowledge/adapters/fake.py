"""`FakeStorage`: the in-memory tree behind every storage adapter in fakes mode (P1-14).

It keeps the port's promises exactly (create-only and etag-matched writes, no-clobber
moves, safe paths, paged listings), so the shared contract suite runs against it too.
Etags are sha256 hex, as on a server path. Scripting: `script(health=...)` sets what
`health()` answers; `calls` records every (operation, path).
"""

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime

from tumnis.core.clock import Clock, SystemClock
from tumnis.modules.knowledge.rules import etag_equal, safe_rel_path
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
        self.folders: set[str] = set()  # what ensure_folder made

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

    async def ensure_folder(self, path: str) -> None:
        path = safe_rel_path(path)
        self.calls.append(("ensure_folder", path))
        self.folders.add(path)

    async def health(self) -> Health:
        return self._health
