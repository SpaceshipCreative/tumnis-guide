"""Database engines and sessions per role (P0-02, P0-06, ADR-0009).

`configure` records the URLs; engines are built on first use.

- `app`: the app role through PgBouncer in production (`DATABASE_URL`). Transaction
  pooling is safe because the workspace context is transaction-local (tumnis.core.tenancy);
  psycopg prepares a statement after five executions and PgBouncer 1.21+ tracks prepared
  statements (`max_prepared_statements` in deploy/pgbouncer/pgbouncer.ini), so the
  default psycopg settings stand.
- `direct`: the app role straight to Postgres, for DBOS and LISTEN (`DATABASE_DIRECT_URL`).
- `owner`: the owner role (`DATABASE_OWNER_URL`), for migrations and test-only resets;
  never used to serve requests.

Each event loop gets its own engine per role. An AsyncEngine must not be shared between
loops (SQLAlchemy, "Using multiple asyncio event loops"): its pool and its first-connect
mutex are asyncio objects bound to one loop. The worker runs two loops, its own (relay,
watchers, cache listener) and DBOS's background loop for enqueued workflows; one shared
engine let a watcher and a workflow step race its first connection and hang the step
(T-P0-07-05's drain stall).
"""

import asyncio
import threading
import weakref
from dataclasses import dataclass, field

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool


@dataclass
class _State:
    app_url: str | None = None
    direct_url: str | None = None
    owner_url: str | None = None
    pooled: bool = True
    # role -> engine, per event loop; `unbound` serves callers outside any running loop
    by_loop: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, AsyncEngine]]" = field(
        default_factory=weakref.WeakKeyDictionary
    )
    unbound: dict[str, AsyncEngine] = field(default_factory=dict)
    # (pool_size, max_overflow) for the engines a loop builds, where it asked for less
    limits: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[int, int]]" = field(
        default_factory=weakref.WeakKeyDictionary
    )


_state = _State()
_lock = threading.Lock()  # the worker's two loops build engines from two threads


def configure(
    app_url: str,
    direct_url: str | None = None,
    *,
    owner_url: str | None = None,
    pooled: bool = True,
) -> None:
    """Point the engines at new URLs. `pooled=False` (tests) keeps no idle connections, so a
    per-test database can be dropped as soon as its sessions close."""
    _state.app_url = app_url
    _state.direct_url = direct_url or app_url
    _state.owner_url = owner_url
    _state.pooled = pooled
    with _lock:
        _state.by_loop = weakref.WeakKeyDictionary()
        _state.unbound = {}


def limit_pools(*, pool_size: int, max_overflow: int) -> None:
    """Cap the pools of the engines the running loop builds from now on. The worker's own
    loop calls it: only the relay and the watchers use it, while DBOS's loop runs the
    workflows with SQLAlchemy's default pools (5 + 10 overflow)."""
    loop = asyncio.get_running_loop()
    with _lock:
        _state.limits[loop] = (pool_size, max_overflow)


def _build(url: str | None, role: str, loop: asyncio.AbstractEventLoop | None) -> AsyncEngine:
    if url is None:
        raise RuntimeError(f"tumnis.core.db has no {role} URL; call configure() first")
    if not _state.pooled:
        return create_async_engine(url, poolclass=NullPool)
    limits = _state.limits.get(loop) if loop is not None else None
    if limits is None:
        return create_async_engine(url, pool_pre_ping=True)
    pool_size, max_overflow = limits
    return create_async_engine(
        url, pool_pre_ping=True, pool_size=pool_size, max_overflow=max_overflow
    )


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _engine(role: str, url: str | None) -> AsyncEngine:
    """The running loop's engine for `role`, built on first use in that loop."""
    loop = _running_loop()
    with _lock:
        engines = _state.unbound if loop is None else _state.by_loop.setdefault(loop, {})
        engine = engines.get(role)
        if engine is None:
            engine = engines[role] = _build(url, role, loop)
        return engine


def app_engine() -> AsyncEngine:
    return _engine("app", _state.app_url)


def direct_engine() -> AsyncEngine:
    return _engine("direct", _state.direct_url)


def owner_engine() -> AsyncEngine:
    return _engine("owner", _state.owner_url)


def app_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Sessions as the app role. Use tenancy.tenant_session for tenant data: a plain app
    session has no workspace in context and sees no tenant rows."""
    return async_sessionmaker(app_engine(), expire_on_commit=False)


def direct_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(direct_engine(), expire_on_commit=False)


def direct_dsn() -> str:
    """The direct URL as a libpq DSN, for a plain psycopg connection (the relay's LISTEN)."""
    if _state.direct_url is None:
        raise RuntimeError("tumnis.core.db has no direct URL; call configure() first")
    url = make_url(_state.direct_url).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


def owner_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(owner_engine(), expire_on_commit=False)


async def dispose() -> None:
    """Close the running loop's pooled connections (and those of engines built outside any
    loop) and forget those engines; the next use in this loop builds new ones. Each loop
    disposes what it built: an engine's connections belong to the loop that opened them,
    and another loop may still be using its own. The api disposes in its lifespan and each
    CLI command before its loop ends; the worker's two loops live as long as the process."""
    loop = asyncio.get_running_loop()
    with _lock:
        engines = [*_state.by_loop.pop(loop, {}).values(), *_state.unbound.values()]
        _state.unbound = {}
    for engine in engines:
        await engine.dispose()
