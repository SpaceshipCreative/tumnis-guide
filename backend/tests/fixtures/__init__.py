"""Shared fixtures (A5), loaded for the whole rootdir by backend/conftest.py.

Core and module tests live under tumnis/**/tests (R-16), outside backend/tests, so the
shared fixtures live in this plugin rather than in backend/tests/conftest.py.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from tests._pg import APP, DbUrls, bootstrap_roles, build_template, clone, drop
from tumnis.core.adapters.registry import AdapterMode, health_states, registered, resolve
from tumnis.core.clock import FixedClock

if TYPE_CHECKING:
    import httpx
    from dbos import DBOS
    from fastapi import FastAPI
    from sqlalchemy.engine import Engine
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
    from testcontainers.community.postgres import PostgresContainer

    from tumnis.core.tenancy import WorkspaceContext
    from tumnis.seed import SeedResult
    from tumnis.settings import Settings

BACKEND = Path(__file__).resolve().parents[2]
SEED_SET = BACKEND / "fixtures" / "seed"
LOAD_SET = BACKEND / "fixtures" / "load" / "load.yaml"
PG_IMAGE = "pgvector/pgvector:pg18"
# Where recordings(provider) looks for a <provider>/ folder. The harness folder holds the
# demo set its own contract test reads.
RECORDING_ROOTS = ("tumnis/modules/*/tests/recordings", "tests/harness/recordings")

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

    def adapter_health(self) -> dict[str, str]:
        """Adapter name -> "ok" or "degraded", over every live tracked instance (P0-09)."""
        return dict(health_states())


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


# --- Workspaces (P0-06) ---------------------------------------------------------------


@dataclass(frozen=True)
class WorkspaceHandle:
    """A workspace made for a test: its id, name and the context to act in it."""

    id: uuid.UUID
    name: str
    ctx: WorkspaceContext


def make_workspace(db: DbUrls, name: str = "Test", timezone: str = "America/New_York") -> uuid.UUID:
    """Insert a workspace as the owner role and return its id (P0-06 spec stub)."""
    raise NotImplementedError("P0-06")


@dataclass(frozen=True)
class PgBouncer:
    """A PgBouncer in transaction mode in front of pg_container (P0-06 spec stub)."""

    host: str
    port: int

    def libpq(self, role: str, dbname: str) -> str:
        raise NotImplementedError("P0-06")


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


# --- Seed and load sets in the per-test database -----------------------------------------


async def _load_set(path: Path, db: DbUrls, clock: FixedClock) -> SeedResult:
    """Through DatabaseSink, i.e. each module's api; works once the entity writers exist
    (projects P0-17, tasks P0-18, events P0-12, documents P0-17)."""
    from tumnis.core import db as core_db  # noqa: PLC0415
    from tumnis.seed import DatabaseSink, load_seed  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, pooled=False)
    return await load_seed(path, DatabaseSink(), anchor=clock.now().date(), clock=clock)


@pytest.fixture
async def seed(db: DbUrls, clock: FixedClock) -> SeedResult:
    """The seed set (3 projects, 30 tasks, one calendar day) in `db`, anchored on the clock."""
    return await _load_set(SEED_SET, db, clock)


@pytest.fixture
async def load_fixture(db: DbUrls, clock: FixedClock) -> SeedResult:
    """The 2,000-task load set in `db`."""
    return await _load_set(LOAD_SET, db, clock)


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


# --- The FastAPI app and an HTTP client on it (P0-04) -------------------------------------


def settings_for(db: DbUrls, dbos_db: DbUrls | None = None, **overrides: Any) -> Settings:
    """Deployment settings pointing at the per-test database with fakes (the plan's
    `test_settings(db)`; renamed so pytest does not collect it as a test)."""
    from tumnis.settings import Settings  # noqa: PLC0415

    values: dict[str, Any] = {
        "database_url": db.app,
        "database_direct_url": db.app,
        "database_owner_url": db.owner,
        "dbos_system_database_url": dbos_db.url(APP) if dbos_db else None,
        "deployment_env": "dev",
        "tumnis_adapters": "fake",
        **overrides,
    }
    return Settings(**values)


@pytest.fixture
async def app(
    db: DbUrls, dbos_sys_db: DbUrls, clock: FixedClock, fakes: Fakes
) -> AsyncIterator[FastAPI]:
    """create_app on the per-test database with fakes; engines disposed afterwards."""
    from tumnis.app import create_app  # noqa: PLC0415
    from tumnis.core import db as core_db  # noqa: PLC0415

    try:
        yield create_app(settings=settings_for(db, dbos_sys_db), clock=clock)
    finally:
        await core_db.dispose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """httpx client on the app in-process; an HTTPS base so Secure cookies round-trip."""
    import httpx  # noqa: PLC0415

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as http:
        yield http


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


# --- Recordings --------------------------------------------------------------------------

Recording = tuple[dict[str, Any], list[Any]]


def load_recordings(provider: str) -> list[Recording]:
    """(raw, expected) pairs from <root>/<provider>/*.json, sorted by file name; each file
    is {"raw": {...}, "expected": [...]}."""
    folders = [
        folder
        for root in RECORDING_ROOTS
        for folder in sorted(BACKEND.glob(f"{root}/{provider}"))
        if folder.is_dir()
    ]
    if not folders:
        raise FileNotFoundError(f"no recordings folder for provider {provider!r}")
    if len(folders) > 1:
        raise ValueError(f"provider {provider!r} has recordings in several modules: {folders}")
    pairs: list[Recording] = []
    for path in sorted(folders[0].glob("*.json")):
        data = json.loads(path.read_text())
        pairs.append((data["raw"], data["expected"]))
    return pairs


@pytest.fixture
def recordings() -> Callable[[str], list[Recording]]:
    """recordings("google_calendar") -> [(raw, expected), ...]."""
    return load_recordings
