"""One cache interface: keys prefixed with the workspace, the in-process backend, the cache
registry and invalidation through LISTEN/NOTIFY (P0-08, Caching, Hosted readiness).

Every key names its workspace (`ws:<uuid>:<namespace>:...`) unless its namespace is
registered with scope "system" (`sys:<namespace>:...`, for lookups made before the
workspace is known). `CacheKey` validation is the only thing standing between a hosted
tenant and another's entries, which is why a malformed key raises instead of logging.

Every cache registers a `CacheSpec` naming its invalidation rule and gets the shared
write-visible test for free (tumnis/core/tests/unit/test_cache.py). Hit and miss counters
are exported per cache name (tumnis.core.metrics).
"""

import asyncio
import contextlib
import json
import logging
import re
import threading
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol, Self
from uuid import UUID

import psycopg
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import Session

from tumnis.core.clock import Clock, SystemClock
from tumnis.core.metrics import CACHE_HITS, CACHE_MISSES

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_NAMESPACE = r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*"
_WS_KEY = re.compile(rf"ws:{_UUID}:({_NAMESPACE}):.*")
_SYS_KEY = re.compile(rf"sys:({_NAMESPACE}):.*")


class InvalidCacheKey(ValueError):  # noqa: N818  # the plan's name
    """A key without the workspace prefix, or a system key of an unregistered namespace."""


def _is_system_namespace(value: str) -> bool:
    match = _SYS_KEY.fullmatch(value)
    spec = _SPECS.get(match.group(1)) if match else None
    return spec is not None and spec.scope == "system"


@dataclass(frozen=True)
class CacheKey:
    value: str

    @classmethod
    def for_workspace(cls, workspace_id: UUID, namespace: str, *parts: str) -> Self:
        return cls(f"ws:{workspace_id}:{namespace}:{':'.join(parts)}")

    @classmethod
    def system(cls, namespace: str, *parts: str) -> Self:
        """Only for namespaces registered with scope="system" (for example
        auth.api_key_by_prefix, where the workspace is not known before the lookup)."""
        return cls(f"sys:{namespace}:{':'.join(parts)}")

    def __post_init__(self) -> None:
        if not (_WS_KEY.fullmatch(self.value) or _is_system_namespace(self.value)):
            raise InvalidCacheKey(self.value)

    @property
    def namespace(self) -> str:
        match = _WS_KEY.fullmatch(self.value) or _SYS_KEY.fullmatch(self.value)
        assert match is not None  # noqa: S101  # checked in __post_init__
        return match.group(1)


class Cache(Protocol):
    async def get(self, key: CacheKey) -> bytes | None: ...
    async def set(
        self, key: CacheKey, value: bytes, *, ttl_s: float | None = None, tags: Sequence[str] = ()
    ) -> None: ...
    async def invalidate(self, key: CacheKey) -> None: ...  # local drop + NOTIFY
    async def invalidate_tag(self, tag: str) -> None: ...


# Sends a JSON invalidation payload to every process ({"k": key} or {"t": tag}).
Publisher = Callable[[str], Awaitable[None]]


@dataclass
class _Entry:
    value: bytes
    expires_at: datetime | None
    tags: tuple[str, ...]


class InProcessCache:
    """An LRU with TTLs read from the injected Clock, and a tag index. The drop methods are
    local only (the invalidation listener calls them); `invalidate` and `invalidate_tag`
    also publish to the other processes when a publisher is set."""

    def __init__(
        self, clock: Clock, max_entries: int = 10_000, *, publish: Publisher | None = None
    ) -> None:
        self._clock = clock
        self._max = max_entries
        self._publish = publish
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._tags: dict[str, set[str]] = {}
        self._generation = 0
        self._lock = threading.Lock()  # DBOS runs workflow steps on other threads

    async def get(self, key: CacheKey) -> bytes | None:
        with self._lock:
            entry = self._entries.get(key.value)
            if entry is None:
                return None
            if entry.expires_at is not None and self._clock.now() >= entry.expires_at:
                self._remove(key.value)
                return None
            self._entries.move_to_end(key.value)
            return entry.value

    async def set(
        self, key: CacheKey, value: bytes, *, ttl_s: float | None = None, tags: Sequence[str] = ()
    ) -> None:
        with self._lock:
            self._store(key.value, value, ttl_s, tuple(tags))

    def token(self) -> int:
        """Taken before reading the source of an entry; `fill` stores only if nothing was
        invalidated in between, so a read racing a write never caches the old value."""
        return self._generation

    async def fill(
        self,
        key: CacheKey,
        value: bytes,
        *,
        since: int,
        ttl_s: float | None = None,
        tags: Sequence[str] = (),
    ) -> None:
        with self._lock:
            if self._generation == since:
                self._store(key.value, value, ttl_s, tuple(tags))

    async def invalidate(self, key: CacheKey) -> None:
        self.drop(key.value)
        if self._publish is not None:
            await self._publish(notify_payload(key=key))

    async def invalidate_tag(self, tag: str) -> None:
        self.drop_tag(tag)
        if self._publish is not None:
            await self._publish(notify_payload(tag=tag))

    def drop(self, key: str) -> None:
        with self._lock:
            self._generation += 1
            self._remove(key)

    def drop_tag(self, tag: str) -> None:
        with self._lock:
            self._generation += 1
            for key in self._tags.pop(tag, set()):
                self._remove(key)

    def clear(self) -> None:
        with self._lock:
            self._generation += 1
            self._entries.clear()
            self._tags.clear()

    def _store(self, key: str, value: bytes, ttl_s: float | None, tags: tuple[str, ...]) -> None:
        self._remove(key)
        expires = None if ttl_s is None else self._clock.now() + timedelta(seconds=ttl_s)
        self._entries[key] = _Entry(value, expires, tags)
        for tag in tags:
            self._tags.setdefault(tag, set()).add(key)
        while len(self._entries) > self._max:
            self._remove(next(iter(self._entries)))

    def _remove(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry is None:
            return
        for tag in entry.tags:
            keys = self._tags.get(tag)
            if keys is not None:
                keys.discard(key)
                if not keys:
                    del self._tags[tag]


# --- The process-wide backend ------------------------------------------------------------

_backend = InProcessCache(SystemClock())


def backend() -> InProcessCache:
    return _backend


def configure_backend(cache: InProcessCache) -> None:
    """The api and the worker set theirs at start (CACHE_BACKEND=memory)."""
    global _backend  # noqa: PLW0603  # one backend per process, like the engines in db
    _backend = cache


@contextmanager
def use_backend(cache: InProcessCache) -> Iterator[InProcessCache]:
    """Swap the process-wide backend for the block (tests)."""
    previous = _backend
    configure_backend(cache)
    try:
        yield cache
    finally:
        configure_backend(previous)


# --- The cache registry --------------------------------------------------------------------


@dataclass(frozen=True)
class CacheSpec:
    name: str  # the key namespace: "settings", "module_flags", "auth.api_key_by_prefix"
    scope: Literal["workspace", "system"]
    ttl_s: float | None
    invalidated_by: tuple[str, ...]  # written rule: event names, table names or "put_setting"


class NamedCache:
    """A registered cache on the process-wide backend: its TTL, its key namespace and its
    hit and miss counters."""

    def __init__(self, spec: CacheSpec) -> None:
        self.spec = spec

    def _check(self, key: CacheKey) -> None:
        if key.namespace != self.spec.name:
            raise InvalidCacheKey(f"{key.value} is not in the {self.spec.name} namespace")

    async def get(self, key: CacheKey) -> bytes | None:
        self._check(key)
        value = await backend().get(key)
        (CACHE_MISSES if value is None else CACHE_HITS).labels(cache=self.spec.name).inc()
        return value

    async def set(self, key: CacheKey, value: bytes, *, tags: Sequence[str] = ()) -> None:
        self._check(key)
        await backend().set(key, value, ttl_s=self.spec.ttl_s, tags=tags)

    def token(self) -> int:
        return backend().token()

    async def fill(
        self, key: CacheKey, value: bytes, *, since: int, tags: Sequence[str] = ()
    ) -> None:
        """`set` unless something was invalidated since `token()` was taken."""
        self._check(key)
        await backend().fill(key, value, since=since, ttl_s=self.spec.ttl_s, tags=tags)

    async def invalidate(self, key: CacheKey) -> None:
        self._check(key)
        await backend().invalidate(key)

    async def invalidate_tag(self, tag: str) -> None:
        await backend().invalidate_tag(tag)


_SPECS: dict[str, CacheSpec] = {}
_NAMED: dict[str, NamedCache] = {}


def register_cache(spec: CacheSpec) -> NamedCache:
    """Registers a cache once per name (registering the same spec again is a no-op). A spec
    without an invalidation rule is refused."""
    if not re.fullmatch(_NAMESPACE, spec.name):
        raise ValueError(f"cache name {spec.name!r} is not a key namespace")
    if not spec.invalidated_by:
        raise ValueError(f"cache {spec.name} names no invalidation rule")
    existing = _SPECS.get(spec.name)
    if existing is not None and existing != spec:
        raise ValueError(f"cache {spec.name} is already registered as {existing}")
    _SPECS[spec.name] = spec
    return _NAMED.setdefault(spec.name, NamedCache(spec))


def registered_caches() -> tuple[CacheSpec, ...]:
    return tuple(_SPECS.values())


def named_cache(name: str) -> NamedCache:
    return _NAMED[name]


# --- Invalidation across processes ---------------------------------------------------------

CHANNEL = "cache_invalidate"
NOTIFY_LIMIT = 8_000  # bytes; Postgres refuses larger NOTIFY payloads


def notify_payload(key: CacheKey | None = None, tag: str | None = None) -> str:
    """{"k": <key>} or {"t": <tag>}, refused above the NOTIFY payload limit."""
    if (key is None) == (tag is None):
        raise ValueError("invalidate one key or one tag")
    payload = json.dumps({"k": key.value} if key is not None else {"t": tag})
    if len(payload.encode()) >= NOTIFY_LIMIT:
        raise ValueError(f"invalidation payload over {NOTIFY_LIMIT} bytes")
    return payload


_PENDING = "tumnis_cache_pending"  # Session.info: local drops waiting for the commit
_NOTIFY = text("SELECT pg_notify(:channel, :payload)")


async def invalidate_on_commit(
    session: AsyncSession, key: CacheKey | None = None, tag: str | None = None
) -> None:
    """pg_notify('cache_invalidate', json) inside the writer's transaction, so other
    processes drop the entry only after the write is visible; the local entry is dropped in
    an after_commit hook. A rollback sends nothing and drops nothing."""
    payload = notify_payload(key, tag)
    await session.execute(_NOTIFY, {"channel": CHANNEL, "payload": payload})
    session.info.setdefault(_PENDING, []).append(payload)


def apply_payload(cache: InProcessCache, payload: str) -> None:
    """Drop what an invalidation payload names from the local backend."""
    body = json.loads(payload)
    if "k" in body:
        cache.drop(body["k"])
    elif "t" in body:
        cache.drop_tag(body["t"])


@event.listens_for(Session, "after_commit")
def _drop_after_commit(session: Session) -> None:
    for payload in session.info.pop(_PENDING, ()):
        apply_payload(backend(), payload)


@event.listens_for(Session, "after_rollback")
def _forget_after_rollback(session: Session) -> None:
    session.info.pop(_PENDING, None)


# --- The listener in every process ---------------------------------------------------------

POLL_S = 1.0  # how often the listener looks at its stop flag while idle
RETRY_S, RETRY_MAX_S = 0.5, 30.0  # reconnect backoff (plan defaults)
_log = logging.getLogger(__name__)


def libpq_url(url: str) -> str:
    """A SQLAlchemy URL (postgresql+psycopg://...) as a libpq one for psycopg."""
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


class CacheInvalidationListener:
    """LISTEN cache_invalidate on a direct connection (never PgBouncer) and drop the named
    keys or tags from the local backend. Runs in the api (lifespan) and the worker (beside
    the outbox relay). After
    every (re)connect it clears the whole local cache: a notification sent while it was not
    listening is lost, so anything cached may be stale (safe, just colder)."""

    def __init__(self, url: str, cache: InProcessCache | None = None) -> None:
        self._dsn = libpq_url(url)
        self._cache = cache
        self.ready = asyncio.Event()

    def _local(self) -> InProcessCache:
        return self._cache if self._cache is not None else backend()

    async def run(self, stop: asyncio.Event) -> None:
        delay = RETRY_S
        while not stop.is_set():
            try:
                async with await psycopg.AsyncConnection.connect(
                    self._dsn, autocommit=True
                ) as conn:
                    # nosemgrep: tumnis-sql-fstring  # CHANNEL is a module constant
                    await conn.execute(f"LISTEN {CHANNEL}")
                    self._local().clear()
                    self.ready.set()
                    delay = RETRY_S
                    while not stop.is_set():
                        async for notify in conn.notifies(timeout=POLL_S):
                            apply_payload(self._local(), notify.payload)
            except (psycopg.OperationalError, OSError):
                self.ready.clear()
                _log.warning("cache invalidation listener lost its connection; retrying")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), delay)
                delay = min(delay * 2, RETRY_MAX_S)


def pg_publisher(engine: Callable[[], AsyncEngine]) -> Publisher:
    """Publishes an invalidation outside any writer's transaction (Cache.invalidate)."""

    async def publish(payload: str) -> None:
        async with engine().begin() as conn:
            await conn.execute(_NOTIFY, {"channel": CHANNEL, "payload": payload})

    return publish
