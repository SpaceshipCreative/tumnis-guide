"""Fakes for an S3 linked source (P3-13): `FakeS3Source`, an in-memory bucket with
`put`/`remove` helpers (ETags are the MD5 of the body, as S3 gives for a single-part put),
and `FakeKeyCapabilities`, scripted per key (read-only and scoped unless scripted)."""

import hashlib
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime

from tumnis.core.adapters.errors import AdapterRejected
from tumnis.core.clock import Clock, SystemClock
from tumnis.modules.knowledge.rules import KeyCapabilities, safe_rel_path
from tumnis.modules.knowledge.storage import (
    LIST_PAGE_SIZE,
    FileStat,
    Health,
    NotFound,
    Page,
    safe_prefix,
)

CHUNK = 64 * 1024


@dataclass(frozen=True)
class _Object:
    data: bytes
    mtime: datetime
    etag: str


class FakeS3Source:
    def __init__(self, clock: Clock | None = None, *, page_size: int = LIST_PAGE_SIZE) -> None:
        self._clock = clock or SystemClock()
        self._page_size = page_size
        self._objects: dict[str, _Object] = {}
        self._health = Health.ok()
        self.calls: list[tuple[str, str]] = []

    # Scripting -------------------------------------------------------------------------

    def put(self, key: str, data: bytes) -> None:
        etag = hashlib.md5(data, usedforsecurity=False).hexdigest()  # S3's single-part ETag
        self._objects[safe_rel_path(key)] = _Object(data, self._clock.now(), etag)

    def remove(self, key: str) -> None:
        self._objects.pop(key, None)

    def script(self, *, health: Health | None = None) -> None:
        if health is not None:
            self._health = health

    # The port --------------------------------------------------------------------------

    def _stat(self, key: str) -> FileStat | None:
        found = self._objects.get(key)
        if found is None:
            return None
        return FileStat(path=key, size=len(found.data), mtime=found.mtime, etag=found.etag)

    async def list(self, prefix: str, cursor: str | None) -> Page[FileStat]:
        prefix = safe_prefix(prefix)
        self.calls.append(("list", prefix))
        keys = sorted(
            k for k in self._objects if k.startswith(prefix) and (not cursor or k > cursor)
        )
        page = keys[: self._page_size]
        items = [st for k in page if (st := self._stat(k)) is not None]
        more = len(keys) > len(page)
        return Page[FileStat](items=items, next_cursor=page[-1] if more and page else None)

    async def stat(self, key: str) -> FileStat | None:
        key = safe_rel_path(key)
        self.calls.append(("stat", key))
        return self._stat(key)

    async def read(self, key: str) -> AsyncIterator[bytes]:
        key = safe_rel_path(key)
        self.calls.append(("read", key))
        found = self._objects.get(key)
        if found is None:
            raise NotFound(key)
        for start in range(0, len(found.data), CHUNK):
            yield found.data[start : start + CHUNK]

    async def health(self) -> Health:
        self.calls.append(("health", ""))
        return self._health

    async def aclose(self) -> None:
        return None


READ_ONLY: KeyCapabilities = KeyCapabilities(
    checked=True,
    can_read=True,
    can_list=True,
    can_write=False,
    can_delete=False,
    bucket_scoped=True,
    prefix=None,
    source="none",
)


class FakeKeyCapabilities:
    """Answers per key: `script(key, caps)` sets one, `refuse(key)` makes the provider
    reject it; any other key is read-only and scoped (with the provider's source)."""

    def __init__(self) -> None:
        self._answers: dict[str, KeyCapabilities | None] = {}
        self.calls: list[tuple[str, str]] = []

    def script(self, key: str, caps: KeyCapabilities) -> None:
        self._answers[key] = caps

    def refuse(self, key: str) -> None:
        self._answers[key] = None

    def _answer(self, op: str, key: str, source: str) -> KeyCapabilities:
        self.calls.append((op, key))
        if key in self._answers:
            found = self._answers[key]
            if found is None:
                raise AdapterRejected("knowledge.key_capabilities", op, "key_rejected")
            return found
        return READ_ONLY.model_copy(update={"source": source})

    async def check_b2(self, key_id: str, application_key: str, *, bucket: str) -> KeyCapabilities:
        return self._answer("b2_authorize_account", key_id, "b2_authorize_account")

    async def check_minio(  # where, who, and what the key is for
        self,
        endpoint: str,
        region: str,
        access_key: str,
        secret_key: str,
        *,
        bucket: str,
        prefixes: Sequence[str],
    ) -> KeyCapabilities:
        return self._answer("minio_account_info", access_key, "minio_account_info")
