"""Database engines and sessions (P0-02). P0-06 adds the owner/app role split and tenancy.

`configure` records the URLs; engines are built on first use. `app` goes through PgBouncer
in production (`DATABASE_URL`); `direct` bypasses it for DBOS and LISTEN
(`DATABASE_DIRECT_URL`).
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
    pooled: bool = True
    app: AsyncEngine | None = None
    direct: AsyncEngine | None = None


_state = _State()


def configure(app_url: str, direct_url: str | None = None, *, pooled: bool = True) -> None:
    """Point the engines at new URLs. `pooled=False` (tests) keeps no idle connections, so a
    per-test database can be dropped as soon as its sessions close."""
    _state.app_url = app_url
    _state.direct_url = direct_url or app_url
    _state.pooled = pooled
    _state.app = None
    _state.direct = None


def _build(url: str | None) -> AsyncEngine:
    if url is None:
        raise RuntimeError("tumnis.core.db is not configured; call configure() first")
    if _state.pooled:
        return create_async_engine(url, pool_pre_ping=True)
    return create_async_engine(url, poolclass=NullPool)


def app_engine() -> AsyncEngine:
    if _state.app is None:
        _state.app = _build(_state.app_url)
    return _state.app


def direct_engine() -> AsyncEngine:
    if _state.direct is None:
        _state.direct = _build(_state.direct_url)
    return _state.direct


def app_sessions() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(app_engine(), expire_on_commit=False)


async def dispose() -> None:
    """Close every pooled connection (application shutdown)."""
    for engine in (_state.app, _state.direct):
        if engine is not None:
            await engine.dispose()
    _state.app = None
    _state.direct = None
