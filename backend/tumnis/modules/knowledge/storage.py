"""The storage port: every knowledge-base file lives behind one `StorageBackend` (P1-14,
FR-15.7, SEC-5).

Backends: `ServerPathStorage` (local disk or a mounted share), `S3Storage` (MinIO, B2,
AWS) and `FakeStorage` (an in-memory tree); one contract suite
(`tests/contract/storage_contract.py`) runs against all three.

`write` never overwrites blindly (FR-15.12): `if_match=None` creates only when the path is
absent, `if_match="<etag>"` replaces only while the current etag equals it; anything else
is `PreconditionFailed(current)`. There is no unconditional overwrite. `move` refuses an
existing destination, `delete` of a missing path is a no-op, and every path passes
`rules.safe_rel_path` first (`PathRejected`).
"""

# The names are the plan's shared contract (P1-14 interfaces), not "...Error".
# ruff: noqa: N818

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from typing import Final, Literal, Protocol, Self, runtime_checkable

from pydantic import BaseModel

from tumnis.core.adapters.base import Adapter
from tumnis.modules.knowledge.rules import PathRejected, StorageError, safe_rel_path

__all__ = [
    "LIST_PAGE_SIZE",
    "MAX_FILE_BYTES",
    "FileStat",
    "Health",
    "LocationOffline",
    "NotFound",
    "Page",
    "PathRejected",
    "PreconditionFailed",
    "StorageBackend",
    "StorageError",
    "TooLarge",
    "call_storage",
    "safe_prefix",
    "spool",
]

MAX_FILE_BYTES: Final = 50 * 1024 * 1024  # SEC-10's 50 MB, read as MiB (plan note)
LIST_PAGE_SIZE: Final = 1000  # plan default; S3's own page size


class FileStat(BaseModel, frozen=True):
    path: str
    size: int
    mtime: datetime
    etag: str  # server path: sha256 hex of the content; S3: the ETag without quotes


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None


class Health(BaseModel, frozen=True):
    status: Literal["ok", "degraded"]
    reason: str | None = None  # "marker_missing", "bucket_unreachable", ...

    @classmethod
    def ok(cls) -> Self:
        return cls(status="ok")

    @classmethod
    def degraded(cls, reason: str) -> Self:
        return cls(status="degraded", reason=reason)


class PreconditionFailed(StorageError):
    """The write's precondition did not hold; `current` is what the path holds now (None
    when it holds nothing)."""

    def __init__(self, current: FileStat | None) -> None:
        super().__init__(f"precondition failed (current: {current.etag if current else None})")
        self.current = current


class LocationOffline(StorageError):
    """The location cannot be written now (its marker is missing, the bucket is gone)."""


class NotFound(StorageError):
    """Nothing at that path."""


class TooLarge(StorageError):
    """The data is over MAX_FILE_BYTES (SEC-10)."""


@runtime_checkable
class StorageBackend(Protocol):
    async def stat(self, path: str) -> FileStat | None: ...

    async def list(self, prefix: str, cursor: str | None) -> Page[FileStat]: ...

    def read(self, path: str) -> AsyncIterator[bytes]: ...

    async def write(
        self, path: str, data: AsyncIterator[bytes], if_match: str | None
    ) -> FileStat: ...

    async def move(self, src: str, dst: str) -> None: ...

    async def delete(self, path: str) -> None: ...

    async def ensure_folder(self, path: str) -> None:
        """Make the folder and its parents if missing (P1-15); a no-op where folders are
        only key prefixes (S3). Never writes a file."""
        ...

    async def health(self) -> Health: ...


async def call_storage[T](
    adapter: Adapter, op: str, fn: Callable[[], Awaitable[T]], *, idempotent: bool
) -> T:
    """`adapter.call`, with the storage answers carried past the breaker: a refused path, a
    failed precondition or a missing file is the backend answering, not an outage, so it
    never counts against the circuit and is never retried."""

    async def carried() -> tuple[T | None, StorageError | None]:
        try:
            return await fn(), None
        except StorageError as exc:
            return None, exc

    result, error = await adapter.call(op, carried, idempotent=idempotent)
    if error is not None:
        raise error
    return result  # type: ignore[return-value]  # set whenever error is None


def safe_prefix(prefix: str) -> str:
    """A `list` prefix: empty (the whole location), a folder ending in one '/', or the
    start of a path; each passes `safe_rel_path` like any other path."""
    if not prefix:
        return ""
    if prefix.endswith("/"):
        return safe_rel_path(prefix[:-1]) + "/"
    return safe_rel_path(prefix)


async def spool(data: AsyncIterator[bytes], *, limit: int = MAX_FILE_BYTES) -> bytes:
    """The whole stream in memory, refused past `limit` (files are at most 50 MiB)."""
    parts: list[bytes] = []
    size = 0
    async for chunk in data:
        size += len(chunk)
        if size > limit:
            raise TooLarge(f"over {limit} bytes")
        parts.append(chunk)
    return b"".join(parts)
