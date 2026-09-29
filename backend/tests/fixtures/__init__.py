"""Shared fixtures (A5), loaded for the whole rootdir by backend/conftest.py.

Core and module tests live under tumnis/**/tests (R-16), outside backend/tests, so the
shared fixtures live in this plugin rather than in backend/tests/conftest.py.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from tests._pg import APP, DbUrls, bootstrap_roles, build_template, clone, drop
from tumnis.core.adapters.registry import AdapterMode, registered, resolve
from tumnis.core.clock import FixedClock

if TYPE_CHECKING:
    from dbos import DBOS
    from sqlalchemy.engine import Engine
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
    from testcontainers.community.postgres import PostgresContainer

PG_IMAGE = "pgvector/pgvector:pg18"

# Monday of the US DST start week: the clocks sprang forward the day before.
CLOCK_START = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Integration tests never forget to open the socket block."""
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(pytest.mark.enable_socket)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(CLOCK_START)


class Fakes:
    """Every registered adapter built in one mode, once per test, by name."""

    def __init__(self, mode: AdapterMode = "fake") -> None:
        self.mode = mode
        self._built: dict[str, Any] = {}

    def __getitem__(self, name: str) -> Any:
        if name not in self._built:
            self._built[name] = resolve(name, self.mode)
        return self._built[name]

    def names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in registered())


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> Fakes:
    """Handle to every adapter fake for scripting and assertions (fakes["calendar.google"])."""
    import tumnis.wiring  # noqa: F401, PLC0415

    monkeypatch.setenv("TUMNIS_ADAPTERS", "fake")
    return Fakes(mode="fake")


# --- Postgres: one container and template per xdist worker, one clone per test ---------


@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    from testcontainers.community.postgres import PostgresContainer  # noqa: PLC0415

    with PostgresContainer(
        PG_IMAGE, username="postgres", password="postgres", dbname="postgres", driver=None
    ) as pg:
        bootstrap_roles(pg.get_connection_url())
        yield pg


@pytest.fixture(scope="session")
def pg_base(pg_container: PostgresContainer) -> DbUrls:
    return DbUrls(
        pg_container.get_container_host_ip(), int(pg_container.get_exposed_port(5432)), "postgres"
    )


@pytest.fixture(scope="session")
def db_template(pg_base: DbUrls, worker_id: str) -> str:
    name = f"tumnis_template_{worker_id}"
    build_template(pg_base, name)
    return name


@pytest.fixture
def db(pg_base: DbUrls, db_template: str) -> Iterator[DbUrls]:
    name = f"t_{uuid.uuid4().hex[:12]}"
    urls = clone(pg_base, db_template, name)
    try:
        yield urls
    finally:
        drop(pg_base, name)


async def _session(url: str) -> AsyncIterator[AsyncSession]:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: PLC0415
    from sqlalchemy.pool import NullPool  # noqa: PLC0415

    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


@pytest.fixture
async def owner_session(db: DbUrls) -> AsyncIterator[AsyncSession]:
    async for session in _session(db.owner):
        yield session


@pytest.fixture
async def app_role_session(db: DbUrls) -> AsyncIterator[AsyncSession]:
    async for session in _session(db.app):
        yield session


# --- DBOS: system database per xdist worker, reset per test -------------------------------


@pytest.fixture(scope="session")
def dbos_sys_db(pg_container: PostgresContainer, pg_base: DbUrls, worker_id: str) -> DbUrls:
    """One DBOS system database per xdist worker, owned by the app role."""
    import psycopg  # noqa: PLC0415
    from psycopg import sql  # noqa: PLC0415

    name = f"tumnis_dbos_{worker_id}"
    with psycopg.connect(pg_container.get_connection_url(), autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name), sql.Identifier(APP))
        )
    return DbUrls(pg_base.host, pg_base.port, name)


@pytest.fixture
def dbos(db: DbUrls, dbos_sys_db: DbUrls) -> Iterator[type[DBOS]]:
    """DBOS configured on the worker's system database, emptied, launched; steps reach the
    per-test database through tumnis.core.db."""
    from dbos import DBOS, DBOSConfig  # noqa: PLC0415

    from tumnis.core import db as core_db  # noqa: PLC0415
    from tumnis.worker import register_queues  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, pooled=False)
    DBOS.destroy(destroy_registry=False)
    config: DBOSConfig = {
        "name": "tumnis-test",
        "application_version": "test",
        "system_database_url": dbos_sys_db.url(APP, driver="psycopg"),
    }
    DBOS(config=config)
    DBOS.reset_system_database(truncate=True)
    register_queues()
    DBOS.launch()
    try:
        yield DBOS
    finally:
        DBOS.destroy(destroy_registry=False)


class QueryCounter:
    """Counts the SQL statements the watched engines send (N+1 assertions)."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self._watched: list[Engine] = []

    @property
    def count(self) -> int:
        return len(self.statements)

    def watch(self, engine: Engine | AsyncEngine) -> None:
        from sqlalchemy import event  # noqa: PLC0415
        from sqlalchemy.ext.asyncio import AsyncEngine  # noqa: PLC0415

        sync = engine.sync_engine if isinstance(engine, AsyncEngine) else engine
        event.listen(sync, "before_cursor_execute", self._record)
        self._watched.append(sync)

    def reset(self) -> None:
        self.statements.clear()

    def close(self) -> None:
        from sqlalchemy import event  # noqa: PLC0415

        for sync in self._watched:
            event.remove(sync, "before_cursor_execute", self._record)
        self._watched.clear()

    def _record(self, _conn: object, _cursor: object, statement: str, *_: object) -> None:
        self.statements.append(statement)


@pytest.fixture
def query_counter() -> Iterator[QueryCounter]:
    counter = QueryCounter()
    try:
        yield counter
    finally:
        counter.close()
