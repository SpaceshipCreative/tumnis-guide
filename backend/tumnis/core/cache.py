"""One cache interface: keys prefixed with the workspace, the in-process backend, the cache
registry and invalidation through LISTEN/NOTIFY (P0-08, Caching, Hosted readiness)."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal, Protocol, Self
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.clock import Clock


class InvalidCacheKey(ValueError):  # noqa: N818  # the plan's name
    """A key without the workspace prefix, or a system key of an unregistered namespace."""


@dataclass(frozen=True)
class CacheKey:
    value: str

    @classmethod
    def for_workspace(cls, workspace_id: UUID, namespace: str, *parts: str) -> Self:
        raise NotImplementedError

    @classmethod
    def system(cls, namespace: str, *parts: str) -> Self:
        raise NotImplementedError


class Cache(Protocol):
    async def get(self, key: CacheKey) -> bytes | None: ...
    async def set(
        self, key: CacheKey, value: bytes, *, ttl_s: float | None = None, tags: Sequence[str] = ()
    ) -> None: ...
    async def invalidate(self, key: CacheKey) -> None: ...
    async def invalidate_tag(self, tag: str) -> None: ...


class InProcessCache:
    def __init__(self, clock: Clock, max_entries: int = 10_000) -> None:
        raise NotImplementedError

    async def get(self, key: CacheKey) -> bytes | None:
        raise NotImplementedError

    async def set(
        self, key: CacheKey, value: bytes, *, ttl_s: float | None = None, tags: Sequence[str] = ()
    ) -> None:
        raise NotImplementedError

    async def invalidate(self, key: CacheKey) -> None:
        raise NotImplementedError

    async def invalidate_tag(self, tag: str) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class CacheSpec:
    name: str
    scope: Literal["workspace", "system"]
    ttl_s: float | None
    invalidated_by: tuple[str, ...]


class NamedCache:
    spec: CacheSpec

    async def get(self, key: CacheKey) -> bytes | None:
        raise NotImplementedError

    async def set(self, key: CacheKey, value: bytes, *, tags: Sequence[str] = ()) -> None:
        raise NotImplementedError

    async def invalidate(self, key: CacheKey) -> None:
        raise NotImplementedError


def register_cache(spec: CacheSpec) -> NamedCache:
    raise NotImplementedError


def registered_caches() -> tuple[CacheSpec, ...]:
    raise NotImplementedError


def named_cache(name: str) -> NamedCache:
    raise NotImplementedError


@contextmanager
def use_backend(backend: InProcessCache) -> Iterator[InProcessCache]:
    raise NotImplementedError
    yield backend


async def invalidate_on_commit(
    session: AsyncSession, key: CacheKey | None = None, tag: str | None = None
) -> None:
    raise NotImplementedError
