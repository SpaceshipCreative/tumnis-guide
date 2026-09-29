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
"""

from dataclasses import dataclass

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
    app: AsyncEngine | None = None
    direct: AsyncEngine | None = None
    owner: AsyncEngine | None = None


_state = _State()


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
    _state.app = None
    _state.direct = None
    _state.owner = None


def _build(url: str | None, role: str) -> AsyncEngine:
    if url is None:
        raise RuntimeError(f"tumnis.core.db has no {role} URL; call configure() first")
    if _state.pooled:
        return create_async_engine(url, pool_pre_ping=True)
    return create_async_engine(url, poolclass=NullPool)


def app_engine() -> AsyncEngine:
    if _state.app is None:
        _state.app = _build(_state.app_url, "app")
    return _state.app


def direct_engine() -> AsyncEngine:
    if _state.direct is None:
        _state.direct = _build(_state.direct_url, "direct")
    return _state.direct


def owner_engine() -> AsyncEngine:
    if _state.owner is None:
        _state.owner = _build(_state.owner_url, "owner")
    return _state.owner


def app_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Sessions as the app role. Use tenancy.tenant_session for tenant data: a plain app
    session has no workspace in context and sees no tenant rows."""
    return async_sessionmaker(app_engine(), expire_on_commit=False)


def direct_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(direct_engine(), expire_on_commit=False)


def owner_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(owner_engine(), expire_on_commit=False)


async def dispose() -> None:
    """Close every pooled connection (application shutdown)."""
    for engine in (_state.app, _state.direct, _state.owner):
        if engine is not None:
            await engine.dispose()
    _state.app = None
    _state.direct = None
    _state.owner = None
