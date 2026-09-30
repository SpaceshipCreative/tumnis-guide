"""Shared fixtures (A5), loaded for the whole rootdir by backend/conftest.py.

Core and module tests live under tumnis/**/tests (R-16), outside backend/tests, so the
shared fixtures live in this plugin rather than in backend/tests/conftest.py.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import functools
import gc
import json
import secrets
import threading
import uuid
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import pytest

from tests._pg import APP, DbUrls, bootstrap_roles, build_template, clone, drop
from tests._pg import OWNER as OWNER_ROLE
from tumnis.core.adapters.registry import AdapterMode, health_states, registered, resolve
from tumnis.core.clock import FixedClock

if TYPE_CHECKING:
    import httpx
    from dbos import DBOS, DBOSClient
    from fastapi import FastAPI
    from sqlalchemy.engine import Engine
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
    from testcontainers.community.postgres import PostgresContainer

    from tests._auth import Account, SessionClient
    from tumnis.core.events import EventEnvelope
    from tumnis.core.tenancy import WorkspaceContext
    from tumnis.seed import SeedResult
    from tumnis.settings import Settings

BACKEND = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND.parent
SEED_SET = BACKEND / "fixtures" / "seed"
LOAD_SET = BACKEND / "fixtures" / "load" / "load.yaml"
PG_IMAGE = "pgvector/pgvector:pg18"
# Where recordings(provider) looks for a <provider>/ folder. The harness folder holds the
# demo set its own contract test reads.
RECORDING_ROOTS = ("tumnis/modules/*/tests/recordings", "tests/harness/recordings")

# Monday of the US DST start week: the clocks sprang forward the day before.
CLOCK_START = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Integration tests never forget to open the socket block, and `drill` tests (A0.4)
    run only when the marker expression names them (`pytest -m drill`, the drill
    workflow): every other run deselects them, so no PR job can pick one up. Plain
    collection (`--collect-only`, the traceability job) still lists them."""
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(pytest.mark.enable_socket)
    if "drill" in (config.option.markexpr or "") or config.option.collectonly:
        return
    drills = [item for item in items if item.get_closest_marker("drill")]
    if drills:
        config.hook.pytest_deselected(items=drills)
        items[:] = [item for item in items if not item.get_closest_marker("drill")]


def pytest_collection_finish(session: pytest.Session) -> None:
    """Move what collection built into the garbage collector's permanent generation.

    Collecting the whole suite (every run does, even `-m serial`, which then deselects
    all but a few tests) leaves about half a million tracked objects: every test module,
    item and parametrize id. Each full (generation 2) collection walked all of them, a
    pause of about 330 ms on a CI runner that stops every thread of the process. Inside
    T-P1-07-01's measurement it held the api, the relay and every label in flight at
    once, which was the p95's tail. Frozen, those objects are skipped by every later
    collection (Python docs, `gc.freeze`); what the tests create is still collected."""
    gc.collect()  # garbage from collection is freed, not frozen
    gc.freeze()


@pytest.fixture
def repo_root() -> Path:
    """The repository root (P0-11): generated schemas, fixtures and the frontend hang off it."""
    return REPO_ROOT


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


# A throwaway server: no fsync. Every test creates and drops a database (each DROP forces a
# checkpoint), and one container per xdist worker shares one Docker disk. With fsync on,
# those flushes stalled commits for hundreds of milliseconds, and a DROP for up to a minute
# (seen in teardown), which pushed the relay's 1 s latency tests over budget under load.
# Engines keep no idle connections in tests (NullPool), so every session opens a server
# connection: with the roles' passwords hashed at one SCRAM iteration instead of 4,096,
# each sign-in to the server stops costing two PBKDF2 runs (the role passwords are set
# after start, by bootstrap_roles, so the setting applies to them).
PG_TEST_SETTINGS = (
    "fsync=off",
    "synchronous_commit=off",
    "full_page_writes=off",
    "scram_iterations=1",
)


@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    from testcontainers.community.postgres import PostgresContainer  # noqa: PLC0415

    container = PostgresContainer(
        PG_IMAGE, username="postgres", password="postgres", dbname="postgres", driver=None
    ).with_command(" ".join(f"-c {setting}" for setting in PG_TEST_SETTINGS))
    with container as pg:
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
    # Its user (P0-13): an owner membership, a password and no TOTP secret yet.
    user_id: uuid.UUID | None = None
    email: str | None = None
    password: str | None = None


def make_workspace(db: DbUrls, name: str = "Test", timezone: str = "America/New_York") -> uuid.UUID:
    """Insert a workspace as the owner role (which bypasses row-level security) and return
    its id."""
    import psycopg  # noqa: PLC0415

    from tests._pg import OWNER  # noqa: PLC0415

    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO workspaces (name, timezone) VALUES (%s, %s) RETURNING id", (name, timezone)
        ).fetchone()
    assert row is not None
    workspace_id: uuid.UUID = row[0]
    return workspace_id


@functools.cache
def _test_password_hash() -> str:
    from tests._auth import TEST_PASSWORD  # noqa: PLC0415
    from tumnis.modules.auth.passwords import hash_password  # noqa: PLC0415

    return hash_password(TEST_PASSWORD)


def make_user(db: DbUrls, workspace_id: uuid.UUID) -> tuple[uuid.UUID, str]:
    """An owner user of the workspace (password `tests._auth.TEST_PASSWORD`, no TOTP yet),
    inserted as the owner role; returns (user_id, email)."""
    import psycopg  # noqa: PLC0415

    from tests._pg import OWNER  # noqa: PLC0415

    email = f"user-{workspace_id.hex[-12:]}@example.test"
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO users (email, password_hash, home_workspace_id) VALUES (%s, %s, %s)"
            " RETURNING id",
            (email, _test_password_hash(), workspace_id),
        ).fetchone()
        assert row is not None
        user_id: uuid.UUID = row[0]
        conn.execute(
            "INSERT INTO memberships (workspace_id, user_id, role, created_by)"
            " VALUES (%s, %s, 'owner', 'system')",
            (workspace_id, user_id),
        )
    return user_id, email


@pytest.fixture
def workspace(db: DbUrls) -> Iterator[WorkspaceHandle]:
    """A workspace (name "Test", America/New_York) made as the owner, with its owner user
    (`user_id`, `email`, `password`; no TOTP secret until `enroll_workspace_user`); the
    test runs inside its context as the system actor."""
    from tests._auth import TEST_PASSWORD  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, use_workspace  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415

    ws = make_workspace(db)
    user_id, email = make_user(db, ws)
    handle = WorkspaceHandle(
        ws, "Test", WorkspaceContext(ws, SYSTEM_ACTOR), user_id, email, TEST_PASSWORD
    )
    entered = use_workspace(handle.ctx)
    entered.__enter__()
    try:
        yield handle
    finally:
        # Set up from inside an async test (request.getfixturevalue), the context var was
        # set in the test's own context, which is gone by teardown.
        with contextlib.suppress(ValueError):
            entered.__exit__(None, None, None)


@pytest.fixture
def two_workspaces(db: DbUrls) -> tuple[WorkspaceHandle, WorkspaceHandle]:
    """Workspaces A and B, each with at least one row in every fenced table: the seed set
    once its writers exist (P0-17, P0-18), and `minimal_row` for any table it leaves empty.
    Enters no context: isolation tests choose theirs."""
    import psycopg  # noqa: PLC0415

    from tests._pg import OWNER  # noqa: PLC0415
    from tests.meta._catalog import fenced_tables, tenant_key  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.core.tests.integration.row_factory import insert_row, minimal_row  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415

    pair = tuple(
        WorkspaceHandle(ws, name, WorkspaceContext(ws, SYSTEM_ACTOR))
        for name in ("A", "B")
        for ws in [make_workspace(db, name)]
    )
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        for table in fenced_tables(conn):
            if tenant_key(conn, table) != "workspace_id":
                continue  # the root: each workspace is its own row
            for handle in pair:
                has_row = conn.execute(
                    psycopg.sql.SQL("SELECT 1 FROM {} WHERE workspace_id = %s LIMIT 1").format(
                        psycopg.sql.Identifier(table)
                    ),
                    (handle.id,),
                ).fetchone()
                if has_row is None:
                    insert_row(conn, table, minimal_row(conn, table, handle.id))
    a, b = pair
    return a, b


@dataclass(frozen=True)
class PgBouncer:
    """PgBouncer in transaction mode in front of pg_container, one server connection per
    pool (`default_pool_size = 1`), so consecutive transactions share a backend."""

    host: str
    port: int

    def libpq(self, role: str, dbname: str) -> str:
        from tests._pg import PASSWORDS  # noqa: PLC0415

        return f"postgresql://{role}:{PASSWORDS[role]}@{self.host}:{self.port}/{dbname}"


# Same image as deploy/compose.yaml.
PGBOUNCER_IMAGE = (
    "edoburu/pgbouncer:v1.25.2-p0"
    "@sha256:7d7a27d9e90985cab5cf42256f5c13a3120baa4b055b69df37beb272b89b2340"
)
PGBOUNCER_INI = """\
[databases]
* = host=postgres port=5432

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
auth_type = scram-sha-256
auth_file = /tmp/userlist.txt
pool_mode = transaction
default_pool_size = 1
max_client_conn = 20
max_prepared_statements = 200
ignore_startup_parameters = extra_float_digits
"""


@pytest.fixture(scope="session")
def pgbouncer(pg_container: PostgresContainer) -> Iterator[PgBouncer]:
    """A pinned PgBouncer on a Docker network shared with pg_container (alias
    `postgres`); any database name routes to the test server, so tests connect to their
    own `db` through it."""
    from testcontainers.core.container import DockerContainer  # noqa: PLC0415
    from testcontainers.core.wait_strategies import LogMessageWaitStrategy  # noqa: PLC0415

    from tests._pg import PASSWORDS  # noqa: PLC0415

    docker = pg_container.get_docker_client().client
    network = docker.networks.create(f"tumnis-pgbouncer-{uuid.uuid4().hex[:8]}")
    pg_id = pg_container.get_wrapped_container().id
    network.connect(pg_id, aliases=["postgres"])
    userlist = f'"{APP}" "{PASSWORDS[APP]}"\n'
    container = (
        DockerContainer(PGBOUNCER_IMAGE)
        .with_kwargs(entrypoint=["/bin/sh", "-c"], network=network.name)
        .with_env("PGBOUNCER_INI", PGBOUNCER_INI)
        .with_env("PGBOUNCER_USERS", userlist)
        .with_command(
            [
                'printf "%s" "$PGBOUNCER_INI" > /tmp/pgbouncer.ini'
                ' && printf "%s" "$PGBOUNCER_USERS" > /tmp/userlist.txt'
                " && exec pgbouncer /tmp/pgbouncer.ini"
            ]
        )
        .with_exposed_ports(6432)
        .waiting_for(LogMessageWaitStrategy("process up"))
    )
    try:
        with container as bouncer:
            yield PgBouncer(bouncer.get_container_host_ip(), int(bouncer.get_exposed_port(6432)))
    finally:
        network.disconnect(pg_id)
        network.remove()


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
    import tumnis.wiring  # noqa: F401, PLC0415  # modules register their seed writers
    from tumnis.core import db as core_db  # noqa: PLC0415
    from tumnis.seed import DatabaseSink, load_seed  # noqa: PLC0415

    # Pooled while loading: each record is a session of its own, and a new server
    # connection per record (NullPool) made the 2,000-task load set take most of a minute.
    # The pool is closed on this loop before the engines go back to NullPool.
    core_db.configure(app_url=db.app, direct_url=db.app, pooled=True)
    try:
        sink = DatabaseSink(skip_missing=True)  # kinds whose module has not landed are skipped
        return await load_seed(path, sink, anchor=clock.now().date(), clock=clock)
    finally:
        try:
            await core_db.dispose()
        finally:
            core_db.configure(app_url=db.app, direct_url=db.app, pooled=False)


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
    _restore_queue_rows(dbos_sys_db)
    earlier = set(threading.enumerate())  # a destroyed instance's threads may linger
    DBOS.launch()
    # DBOS 3.1 persists queues in the system database, so they register after launch, and
    # it refuses the sync call inside a running event loop (a test that requests this
    # fixture mid-test): register from a thread of its own.
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(register_queues).result()
        queues = [queue.name for queue in pool.submit(DBOS.list_queues).result()]
    _wait_for_queue_workers(queues, earlier)
    closing = _close_late_checkins()
    try:
        _save_queue_rows(dbos_sys_db)
        yield DBOS
    finally:
        _stop_queue_workers(earlier)
        closing.set()
        DBOS.destroy(destroy_registry=False)


# System database name -> its `dbos.queues` rows (JSON) as register_queues left them.
_QUEUE_ROWS: dict[str, str] = {}


def _save_queue_rows(sys_db: DbUrls) -> None:
    """Keep the queue rows of the worker's first launch (register_queues' queues only: the
    system database was just emptied), for `_restore_queue_rows`."""
    import psycopg  # noqa: PLC0415

    if sys_db.name in _QUEUE_ROWS:
        return
    with psycopg.connect(sys_db.libpq(APP)) as conn:
        row = conn.execute(b"SELECT json_agg(q)::text FROM dbos.queues q").fetchone()
    if row is not None and row[0] is not None:
        _QUEUE_ROWS[sys_db.name] = row[0]


def _restore_queue_rows(sys_db: DbUrls) -> None:
    """Put the queue rows back after the truncate, before launch. DBOS's queue manager
    (dbos 3.1.0) lists the queues when it starts and then once a second, so queues that
    register only after launch got their worker threads up to a second later: that second
    was most of the `dbos` fixture's setup. register_queues still runs after launch and
    upserts the same rows."""
    import psycopg  # noqa: PLC0415

    rows = _QUEUE_ROWS.get(sys_db.name)
    if rows is None:
        return
    with psycopg.connect(sys_db.libpq(APP)) as conn:
        conn.execute(
            b"INSERT INTO dbos.queues SELECT * FROM json_populate_recordset(NULL::dbos.queues, %s)",
            (rows,),
        )


def _wait_for_queue_workers(
    queues: list[str], earlier: set[threading.Thread], timeout_s: float = 10
) -> None:
    """Block until DBOS dequeues from every queue in `queues`. Its queue manager looks for
    new queues once a second and starts a `queue-worker-<name>` thread for each (dbos
    3.1.0), so without this wait the first enqueue in a test sat up to a second longer
    than the queue's polling interval, and timing tests (T-P0-07-07, -08) measured DBOS's
    startup instead of the relay."""
    import time  # noqa: PLC0415

    wanted = {f"queue-worker-{name}" for name in queues}
    deadline = time.monotonic() + timeout_s
    while not wanted <= {t.name for t in threading.enumerate() if t not in earlier}:
        if time.monotonic() > deadline:
            raise TimeoutError(f"DBOS started no worker thread for {sorted(wanted)}")
        time.sleep(0.01)


def _stop_queue_workers(earlier: set[threading.Thread], timeout_s: float = 10) -> None:
    """Quiesce DBOS before DBOS.destroy stops its event loop and disposes its engine (dbos
    3.1.0 does both at once): stop dequeuing, then wait for the workflows a test left
    running. A queue worker that had just dequeued a workflow would hand it to the stopped
    loop (a coroutine never awaited: an unraisable-exception error at teardown), and a
    workflow still running holds pooled system-database connections that dispose() cannot
    close; the garbage collector finds them open later, in another test ("psycopg.Connection
    ... was deleted while still open")."""
    import time  # noqa: PLC0415

    from dbos._dbos import _get_dbos_instance  # noqa: PLC0415  # dbos 3.1.0: no public hook

    instance = _get_dbos_instance()
    for event in instance.background_thread_stop_events:
        event.set()
    for thread in threading.enumerate():
        if thread not in earlier and thread.name.startswith("queue-worker-"):
            thread.join(timeout=timeout_s)
    deadline = time.monotonic() + timeout_s
    while instance._active_workflows_set.activeList() and time.monotonic() < deadline:
        time.sleep(0.01)


def _close_late_checkins() -> threading.Event:
    """Once the returned event is set, a system-database connection checked back in is
    closed rather than pooled (issue #35). DBOS 3.1.0 drops a workflow from its active set
    before it writes the outcome, so when teardown destroys DBOS an executor thread may still
    hold a pooled connection: dispose() cannot close it, and it goes back to the disposed pool
    open, where the garbage collector reports it ("psycopg.Connection ... deleted while still
    open") in whatever test runs next. The listener sits on the engine, so it also covers
    the pool dispose() puts in place of the old one."""
    from dbos._dbos import _get_dbos_instance  # noqa: PLC0415  # dbos 3.1.0: no public hook
    from sqlalchemy import event  # noqa: PLC0415

    closing = threading.Event()

    def close_after_teardown(_dbapi_connection: object, record: Any) -> None:
        if closing.is_set():
            record.invalidate()  # closes the DBAPI connection; a later checkout reconnects

    event.listen(_get_dbos_instance()._sys_db.engine, "checkin", close_after_teardown)
    return closing


@pytest.fixture
def dbos_client(dbos: type[DBOS], dbos_sys_db: DbUrls) -> Iterator[DBOSClient]:
    """A DBOSClient on the worker's system database, as the api process uses one to enqueue
    (P0-07); `dbos` is the executor that runs what it enqueues."""
    from dbos import DBOSClient  # noqa: PLC0415

    client = DBOSClient(system_database_url=dbos_sys_db.url(APP))
    try:
        yield client
    finally:
        client.destroy()


# --- Envelopes for subscriber tests (P0-21) -----------------------------------------------


def make_envelope(
    name: str,
    payload: dict[str, Any],
    workspace: WorkspaceHandle,
    event_id: uuid.UUID | None = None,
    *,
    occurred_at: datetime = CLOCK_START,
) -> EventEnvelope:
    """An envelope as the relay would hand it to a subscriber: version 1, the system actor,
    a fresh uuid4 event id unless one is given. Any subscriber test can call a handler (or
    `deliver_event`) with it, without an emitter or the relay."""
    from tumnis.core.events import EventEnvelope  # noqa: PLC0415

    return EventEnvelope(
        event_id=event_id or uuid.uuid4(),
        name=name,
        schema_version=1,
        workspace_id=workspace.id,
        occurred_at=occurred_at,
        actor="system",
        payload=payload,
    )


# --- Kill-and-resume: a worker in a subprocess, killed at a named step (P0-07) -------------

KILLED_EXIT = 137  # tumnis.core.faults.killpoint exits with this code
KILLER_IMPORTS = ("tumnis.core.tests.integration._deliveries",)
KILLER_APP_VERSION = "worker-killer"  # both processes share it, so the second recovers


class WorkerKiller:
    """Runs `python -m tumnis.testing.run_worker` against the per-test database and its own
    fresh DBOS system database. `run_until_killed()` emits the events, starts a worker with
    TUMNIS_KILLPOINT set and waits for it to die; `restart_and_drain()` starts a clean worker
    and waits until every outbox row is sent and every delivery workflow succeeded."""

    def __init__(
        self,
        killpoint: str,
        *,
        db: DbUrls,
        sys_db: DbUrls,
        events: int,
        event: str,
        logs: Path,
        imports: Sequence[str] = (),
        queues: Sequence[str] = (),
    ) -> None:
        self.killpoint = killpoint
        self.queues = tuple(queues)  # P1-16: `--queues`, a worker that dequeues only these
        self.db = db
        self.sys_db = sys_db
        self.events = events
        self.event = event
        self.logs = logs
        self.imports = (*KILLER_IMPORTS, *imports)
        self.event_ids: list[uuid.UUID] = []
        self._procs: list[asyncio.subprocess.Process] = []
        self._client: DBOSClient | None = None

    async def _emit(self) -> None:
        import importlib  # noqa: PLC0415

        from tumnis.core import db as core_db  # noqa: PLC0415
        from tumnis.core.events import registry  # noqa: PLC0415
        from tumnis.core.outbox import emit  # noqa: PLC0415
        from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
        from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415

        deliveries = importlib.import_module(KILLER_IMPORTS[0])
        deliveries.create_table(self.db.libpq(OWNER_ROLE))
        core_db.configure(app_url=self.db.app, direct_url=self.db.app, pooled=False)
        model = registry.model(self.event, 1)
        ctx = WorkspaceContext(make_workspace(self.db, "Killer"), SYSTEM_ACTOR)
        at = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
        async with tenant_session(ctx) as session:
            for i in range(self.events):
                payload = model.model_validate({"note": f"kill-{i}"})
                self.event_ids.append(await emit(session, payload, occurred_at=at))

    async def _start(self, killpoint: str | None) -> asyncio.subprocess.Process:
        import os  # noqa: PLC0415
        import sys  # noqa: PLC0415

        env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"DATABASE_OWNER_URL", "TUMNIS_KILLPOINT"}
        }
        env |= {
            "DATABASE_URL": self.db.app,
            "DATABASE_DIRECT_URL": self.db.app,
            "DBOS_SYSTEM_DATABASE_URL": self.sys_db.url(APP),
            "DEPLOYMENT_ENV": "dev",
            "TUMNIS_ADAPTERS": "fake",
        }
        if killpoint is not None:
            env["TUMNIS_KILLPOINT"] = killpoint
        args = [sys.executable, "-m", "tumnis.testing.run_worker"]
        for name in self.imports:
            args += ["--import", name]
        args += ["--app-version", KILLER_APP_VERSION]
        if self.queues:
            args += ["--queues", ",".join(self.queues)]
        label = "-".join(self.queues) or "main"  # two killers may share the logs folder
        log = (self.logs / f"worker-{label}-{len(self._procs)}.log").open("wb")
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=BACKEND, env=env, stdout=log, stderr=asyncio.subprocess.STDOUT
        )
        log.close()
        self._procs.append(proc)
        return proc

    async def start_worker(self, *, armed: bool) -> asyncio.subprocess.Process:
        """A worker on the harness databases, with the kill point armed or not, for tests
        that enqueue their own workflow (P0-19) instead of emitting events."""
        return await self._start(self.killpoint if armed else None)

    async def stop_worker(self, proc: asyncio.subprocess.Process) -> None:
        await self._stop(proc)

    def dbos_client(self) -> DBOSClient:
        """A DBOSClient on the harness's system database (closed with the killer)."""
        from dbos import DBOSClient  # noqa: PLC0415

        if self._client is None:
            self._client = DBOSClient(system_database_url=self.sys_db.url(APP))
        return self._client

    def log_tail(self, lines: int = 60) -> str:
        out = []
        for path in sorted(self.logs.glob("worker-*.log")):
            out.append(f"--- {path.name}")
            out += path.read_text(errors="replace").splitlines()[-lines:]
        return "\n".join(out)

    async def run_until_killed(self, timeout_s: float = 30) -> int:
        """Emit the events, run a worker with the kill point armed, return its exit code."""
        await self._emit()
        proc = await self._start(self.killpoint)
        try:
            return await asyncio.wait_for(proc.wait(), timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            pytest.fail(
                f"worker not killed at {self.killpoint} in {timeout_s} s\n{self.log_tail()}"
            )

    async def _migrated(self, proc: asyncio.subprocess.Process, timeout_s: float) -> None:
        """Wait until the worker has created DBOS's system tables (a client never does)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            try:
                await asyncio.to_thread(self.dbos_client().list_workflows, limit=1)
            except Exception:  # tables not there yet
                self.close_client()
            else:
                return
            if proc.returncode is not None or loop.time() > deadline:
                pytest.fail(f"worker never migrated DBOS's tables\n{self.log_tail()}")
            await asyncio.sleep(0.1)

    async def enqueue_until_killed(
        self,
        *,
        queue_name: str,
        workflow_name: str,
        workflow_id: str,
        args: Sequence[Any] = (),
        timeout_s: float = 60,
    ) -> int:
        """Start a worker with the kill point armed, enqueue one workflow on it once DBOS is
        up, and return the worker's exit code (137 when it died at the kill point)."""
        proc = await self._start(self.killpoint)
        await self._migrated(proc, timeout_s)
        options: dict[str, Any] = {
            "queue_name": queue_name,
            "workflow_name": workflow_name,
            "workflow_id": workflow_id,
            "app_version": KILLER_APP_VERSION,
        }
        await self.dbos_client().enqueue_async(options, *args)  # type: ignore[arg-type]
        try:
            return await asyncio.wait_for(proc.wait(), timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            pytest.fail(
                f"worker not killed at {self.killpoint} in {timeout_s} s\n{self.log_tail()}"
            )

    async def restart_until_done(self, workflow_id: str, timeout_s: float = 60) -> str:
        """Start a worker without the kill point, wait until the workflow has left the
        pending and enqueued states (DBOS recovers it), stop the worker; its status."""
        proc = await self._start(None)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        try:
            while True:
                if proc.returncode is not None:
                    pytest.fail(f"worker exited with {proc.returncode}\n{self.log_tail()}")
                status = (
                    await asyncio.to_thread(
                        self.dbos_client().retrieve_workflow(workflow_id).get_status
                    )
                ).status
                if status not in {"PENDING", "ENQUEUED"}:
                    return str(status)
                if loop.time() > deadline:
                    pytest.fail(f"{workflow_id} still {status} after {timeout_s} s")
                await asyncio.sleep(0.1)
        finally:
            await self._stop(proc)

    def close_client(self) -> None:
        if self._client is not None:
            self._client.destroy()
            self._client = None

    def _unsent(self) -> int:
        import psycopg  # noqa: PLC0415

        with psycopg.connect(self.db.libpq(OWNER_ROLE)) as conn:
            row = conn.execute("SELECT count(*) FROM outbox WHERE sent_at IS NULL").fetchone()
        assert row is not None
        return int(row[0])

    def _deliveries(self) -> tuple[dict[str, Any], int]:
        """(workflow_id -> output of each succeeded delivery, number not yet succeeded)."""
        workflows = self.dbos_client().list_workflows(name="deliver_event")
        done = {w.workflow_id: w.output for w in workflows if w.status == "SUCCESS"}
        return done, len(workflows) - len(done)

    async def restart_and_drain(self, timeout_s: float = 30) -> dict[str, Any]:
        """Start a worker without the kill point; wait until every outbox row is sent and
        every delivery workflow the relay enqueued succeeded; stop it. Returns workflow_id ->
        output. The relay enqueues a row's deliveries before it marks the row sent, so once
        none is unsent the list of delivery workflows is complete. The subscribers are the
        subprocess's, never counted from this process's registry, which other tests on this
        xdist worker may have added to."""
        proc = await self._start(None)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        done: dict[str, Any] = {}
        try:
            while True:
                if proc.returncode is not None:
                    pytest.fail(f"worker exited with {proc.returncode}\n{self.log_tail()}")
                unsent = await asyncio.to_thread(self._unsent)
                done, running = await asyncio.to_thread(self._deliveries)
                if unsent == 0 and running == 0:
                    return done
                if loop.time() > deadline:
                    pytest.fail(
                        f"not drained in {timeout_s} s: {unsent} unsent, {len(done)} "
                        f"workflows succeeded, {running} not\n{self.log_tail()}"
                    )
                await asyncio.sleep(0.1)
        finally:
            await self._stop(proc)

    async def start(self, killpoint: str | None = None) -> asyncio.subprocess.Process:
        """A worker on this killer's databases, armed at `killpoint` when one is given, for
        tests that drive their own workflows (P1-04); end it with `stop`."""
        return await self._start(killpoint)

    async def stop(self, proc: asyncio.subprocess.Process) -> None:
        await self._stop(proc)

    @staticmethod
    async def _stop(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 15)
        except TimeoutError:
            proc.kill()
            await proc.wait()

    async def close(self) -> None:
        for proc in self._procs:
            await self._stop(proc)
        self.close_client()


class WorkerKillerFactory(Protocol):
    def __call__(
        self,
        killpoint: str,
        *,
        events: int = 5,
        event: str = "test.ping",
        imports: Sequence[str] = (),
        queues: Sequence[str] = (),
    ) -> WorkerKiller: ...


@pytest.fixture
async def worker_killer(
    db: DbUrls, pg_container: PostgresContainer, pg_base: DbUrls, tmp_path: Path
) -> AsyncIterator[WorkerKillerFactory]:
    """`worker_killer(killpoint, *, events=5, event="test.ping", imports=())` -> a
    WorkerKiller on `db` and a fresh DBOS system database (dropped afterwards), so no
    earlier test's workflows are recovered by the subprocess. `imports` are more modules
    the worker subprocesses import first (a WP's workflows and test probes); `queues` makes
    them dequeue only those queues (`run_worker --queues`, P1-16)."""
    import psycopg  # noqa: PLC0415
    from psycopg import sql  # noqa: PLC0415

    from tumnis.core import db as core_db  # noqa: PLC0415

    name = f"t_dbos_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(pg_container.get_connection_url(), autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name), sql.Identifier(APP))
        )
    sys_db = DbUrls(pg_base.host, pg_base.port, name)
    # DBOS's tables up front, so a test may enqueue through a DBOSClient (which never
    # migrates) before its first worker starts (P1-04).
    from dbos import run_dbos_database_migrations  # noqa: PLC0415

    await asyncio.to_thread(run_dbos_database_migrations, sys_db.url(APP))
    made: list[WorkerKiller] = []

    def factory(
        killpoint: str,
        *,
        events: int = 5,
        event: str = "test.ping",
        imports: Sequence[str] = (),
        queues: Sequence[str] = (),
    ) -> WorkerKiller:
        killer = WorkerKiller(
            killpoint,
            db=db,
            sys_db=sys_db,
            events=events,
            event=event,
            logs=tmp_path,
            imports=imports,
            queues=queues,
        )
        made.append(killer)
        return killer

    try:
        yield factory
    finally:
        for killer in made:
            await killer.close()
        await core_db.dispose()
        drop(pg_base, name)


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


# --- The master key file (P0-08, SEC-6) ---------------------------------------------------


@dataclass(frozen=True)
class MasterKeyFile:
    """A key file written for a test: its path, the raw keys by version and the active one."""

    path: Path
    keys: dict[int, bytes]
    active: int


def write_master_key_file(
    path: Path, keys: Mapping[int, bytes], active: int, mode: int = 0o600
) -> Path:
    """The key file format `tumnis.core.crypto.load_master_keys` reads:
    {"active": <version>, "keys": {"<version>": "<base64 of 32 bytes>"}}, chmod `mode`."""
    body = {
        "active": active,
        "keys": {str(version): base64.b64encode(key).decode() for version, key in keys.items()},
    }
    path.write_text(json.dumps(body))
    path.chmod(mode)
    return path


@pytest.fixture
def master_key_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[MasterKeyFile]:
    """A JSON key file (one fresh key, version 1) with mode 0o600 in tmp_path;
    MASTER_KEY_FILE points at it and tumnis.core.crypto loads its keys."""
    from tumnis.core import crypto  # noqa: PLC0415

    keys = {1: secrets.token_bytes(32)}
    path = write_master_key_file(tmp_path / "master_key.json", keys, active=1)
    monkeypatch.setenv("MASTER_KEY_FILE", str(path))
    crypto.configure_master_keys(lambda: crypto.load_master_keys(str(path), strict_owner=False))
    try:
        yield MasterKeyFile(path, keys, active=1)
    finally:
        crypto.reset_master_keys()


@dataclass(frozen=True)
class PepperFile:
    """The pepper file written for a test (P0-13; P0-14 reuses it): same format and checks
    as the master key file."""

    path: Path
    keys: dict[int, bytes]
    active: int


@pytest.fixture
def pepper_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[PepperFile]:
    """A pepper file (one fresh 32-byte pepper, version 1, mode 0o600) in tmp_path;
    API_KEY_PEPPER_FILE points at it and tumnis.core.crypto loads its peppers."""
    from tumnis.core import crypto  # noqa: PLC0415

    keys = {1: secrets.token_bytes(32)}
    path = write_master_key_file(tmp_path / "pepper.json", keys, active=1)
    monkeypatch.setenv("API_KEY_PEPPER_FILE", str(path))
    crypto.configure_peppers(
        lambda: crypto.load_master_keys(str(path), strict_owner=False, what=crypto.PEPPER_FILE)
    )
    try:
        yield PepperFile(path, keys, active=1)
    finally:
        crypto.reset_peppers()


@pytest.fixture
def app(  # noqa: PLR0917
    db: DbUrls,
    dbos_sys_db: DbUrls,
    clock: FixedClock,
    fakes: Fakes,
    master_key_file: MasterKeyFile,
    pepper_file: PepperFile,
) -> FastAPI:
    """create_app on the per-test database with fakes, a master key file and a pepper
    file. A plain (sync) fixture, so an async test may also reach it through
    `request.getfixturevalue` (T-P0-08-20 asks for `session_client` that way): its engines
    keep no idle connections (NullPool), so there is nothing to dispose afterwards."""
    from tumnis.app import create_app  # noqa: PLC0415
    from tumnis.core import db as core_db  # noqa: PLC0415

    settings = settings_for(db, dbos_sys_db, api_key_pepper_file=str(pepper_file.path))
    built = create_app(settings=settings, clock=clock)
    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    return built


class AppFactory(Protocol):
    def __call__(self, **overrides: Any) -> FastAPI: ...


@pytest.fixture
def app_factory(  # noqa: PLR0917
    db: DbUrls,
    dbos_sys_db: DbUrls,
    clock: FixedClock,
    fakes: Fakes,
    master_key_file: MasterKeyFile,
    pepper_file: PepperFile,
) -> AppFactory:
    """`app_factory(**settings_overrides)`: another create_app on the per-test database
    with fakes and the test clock, for tests that need a second deployment shape (hosted
    mode, P0-13). Like `app`, its engines keep no idle connections."""
    from tumnis.app import create_app  # noqa: PLC0415
    from tumnis.core import db as core_db  # noqa: PLC0415

    def build(**overrides: Any) -> FastAPI:
        overrides.setdefault("api_key_pepper_file", str(pepper_file.path))
        built = create_app(settings=settings_for(db, dbos_sys_db, **overrides), clock=clock)
        core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
        return built

    return build


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """httpx client on the app in-process; an HTTPS base so Secure cookies round-trip."""
    import httpx  # noqa: PLC0415

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as http:
        yield http


@dataclass(frozen=True)
class AppWithFakes:
    """The app with fakes on the per-test database, a workspace and a full-scope API key
    (P0-14): send `principal_headers` (`Authorization: Bearer tmn_...`) with each request."""

    app: FastAPI
    workspace: WorkspaceHandle
    principal_headers: dict[str, str]


@pytest.fixture
def app_with_fakes(  # noqa: PLR0917
    db: DbUrls,
    dbos_sys_db: DbUrls,
    clock: FixedClock,
    fakes: Fakes,
    master_key_file: MasterKeyFile,
    pepper_file: PepperFile,
    workspace: WorkspaceHandle,
) -> Iterator[AppWithFakes]:
    """create_app on `db` with TUMNIS_ADAPTERS=fake, the seed set loaded once its writers
    exist (P0-17, P0-18), and a real API key holding every scope (made through the auth
    api, P0-14) for `workspace`. Rate limits are off: the clock is fixed, so a bucket would
    never refill under the fuzzer's hundreds of requests (P0-10's tests cover the limits).
    A sync fixture: the caller drives the app through its own event loop (Schemathesis
    runs each request in a TestClient, whose lifespan disposes the engines)."""
    import tumnis.wiring  # noqa: F401, PLC0415  # modules register their seed writers
    from tumnis.app import create_app  # noqa: PLC0415
    from tumnis.core import db as core_db  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.core.types import ActorRef  # noqa: PLC0415
    from tumnis.modules.auth import api as auth_api  # noqa: PLC0415
    from tumnis.modules.auth.scopes import SCOPES  # noqa: PLC0415
    from tumnis.seed import writers_registered  # noqa: PLC0415

    if writers_registered():
        asyncio.run(_load_set(SEED_SET, db, clock))
    settings = settings_for(db, dbos_sys_db, api_key_pepper_file=str(pepper_file.path))
    app = create_app(settings=settings, clock=clock)
    app.state.rate_limiter = None
    app.state.auth_lockouts = False  # the same for sign-in lockouts (P0-13)
    ctx = WorkspaceContext(workspace.id, ActorRef(f"user:{workspace.user_id}"))

    async def make_key() -> str:
        body = auth_api.KeyIn(name="fuzzer", scopes=sorted(SCOPES))
        created = await auth_api.create_key(ctx, body, now=clock.now())
        await core_db.dispose()
        return created.key

    key = asyncio.run(make_key())
    try:
        yield AppWithFakes(app, workspace, {"Authorization": f"Bearer {key}"})
    finally:
        asyncio.run(core_db.dispose())


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


# --- Hostile snippets (P2-02, SAF-1; shared with P2-11) ---------------------------------

HOSTILE_SNIPPETS = BACKEND / "fixtures" / "hostile" / "snippets.yaml"


@dataclass(frozen=True)
class HostileSnippet:
    """One hostile example: its text and a plain marker string inside it that must never
    reach a prompt outside an `<untrusted-data>` block."""

    name: str
    why: str
    input: str
    marker: str


@functools.cache
def load_hostile_snippets() -> dict[str, HostileSnippet]:
    """The twelve cases of backend/fixtures/hostile/snippets.yaml, by name (in file order;
    usable at collection time, for parametrize)."""
    import yaml  # noqa: PLC0415

    data = yaml.safe_load(HOSTILE_SNIPPETS.read_text(encoding="utf-8"))
    return {
        name: HostileSnippet(name, case["why"], case["input"], case["marker"])
        for name, case in data["cases"].items()
    }


@pytest.fixture
def hostile_snippets() -> dict[str, HostileSnippet]:
    """hostile_snippets["plain_close"].input -> the text (P2-02, P2-11)."""
    return load_hostile_snippets()


# --- Signed-in clients (P0-13) ----------------------------------------------------------


async def enroll_workspace_user(workspace: WorkspaceHandle, clock: FixedClock) -> Account:
    """Give the `workspace` fixture's user a confirmed TOTP secret (through the auth api)
    and return what signing in as that user needs."""
    import pyotp  # noqa: PLC0415

    from tests._auth import Account  # noqa: PLC0415
    from tumnis.modules.auth import api as auth_api  # noqa: PLC0415

    assert workspace.user_id is not None
    assert workspace.email is not None
    assert workspace.password is not None
    secret = pyotp.random_base32()
    await auth_api.enroll_totp(workspace.user_id, workspace.id, secret, confirmed_at=clock.now())
    return Account(workspace.email, workspace.password, secret, workspace.user_id, workspace.id)


@pytest.fixture
def session_client(
    app: FastAPI, clock: FixedClock, db: DbUrls, request: pytest.FixtureRequest
) -> SessionClient:
    """An httpx client signed in (password and TOTP at the clock's time) as the
    `workspace` fixture's user, or as a new owner of workspace B when the test uses
    `two_workspaces` (A0.3: B plays the caller's own workspace). It sends `X-CSRF-Token`
    (`session_client.csrf`) and an `Idempotency-Key` on every write that lacks them. The
    clock moves one TOTP step on afterwards, so another sign-in in the test gets a fresh
    code.

    A plain fixture (async tests may ask for it with `request.getfixturevalue`): the
    sign-in runs on an event loop of its own in a helper thread; the in-process app and
    its NullPool engines serve it there as they serve the test later."""
    from tests._auth import (  # noqa: PLC0415
        TEST_PASSWORD,
        TOTP_STEP,
        run_async,
        session_client_for,
        sign_in,
    )

    if "two_workspaces" in request.fixturenames:
        b: WorkspaceHandle = request.getfixturevalue("two_workspaces")[1]
        user_id, email = make_user(db, b.id)
        handle = WorkspaceHandle(b.id, b.name, b.ctx, user_id, email, TEST_PASSWORD)
    else:
        handle = request.getfixturevalue("workspace")
    http = session_client_for(app)

    async def start() -> Account:
        account = await enroll_workspace_user(handle, clock)
        await sign_in(http, account, clock)
        return account

    http.account = run_async(start)
    clock.advance(TOTP_STEP)
    return http


class KeyClientFactory(Protocol):
    async def __call__(
        self, scopes: Any, projects: Any = None
    ) -> httpx.AsyncClient: ...  # a tests._keys.KeyClient


@pytest.fixture
def key_client(
    app: FastAPI, clock: FixedClock, db: DbUrls, request: pytest.FixtureRequest
) -> KeyClientFactory:
    """`await key_client(scopes, projects=None)`: an httpx client on `app` sending
    `Authorization: Bearer <key>` for a fresh API key with those scopes (limited to
    `projects` when given), made through the auth api's `create_key` (P0-14). The key
    belongs to the `workspace` fixture's workspace (made by its user), or to workspace B
    when the test uses `two_workspaces` (A0.3). Writes get an `Idempotency-Key`; the client
    carries `.key` and `.key_id`."""
    from tests._keys import key_client_for  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR, ActorRef  # noqa: PLC0415

    if "two_workspaces" in request.fixturenames:
        b: WorkspaceHandle = request.getfixturevalue("two_workspaces")[1]
        ctx = WorkspaceContext(b.id, SYSTEM_ACTOR)
    else:
        handle: WorkspaceHandle = request.getfixturevalue("workspace")
        actor = ActorRef(f"user:{handle.user_id}") if handle.user_id else SYSTEM_ACTOR
        ctx = WorkspaceContext(handle.id, actor)

    async def make(scopes: Any, projects: Any = None) -> httpx.AsyncClient:
        return await key_client_for(app, ctx, scopes, projects, now=clock.now())

    return make


# --- Traces and JSON logs (P0-27) ----------------------------------------------------------


@pytest.fixture
def span_exporter() -> Iterator[Any]:
    """An InMemorySpanExporter wired through `setup_tracing("api", exporter)`; spans finish
    into it synchronously. Afterwards tracing goes back to no exporter."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: PLC0415
        InMemorySpanExporter,
    )

    from tumnis.core import telemetry  # noqa: PLC0415

    exporter = InMemorySpanExporter()
    telemetry.setup_tracing("api", exporter)
    try:
        yield exporter
    finally:
        telemetry.setup_tracing("api")
        exporter.clear()


class JsonLogs:
    """What the JSON log handler wrote while the fixture was active."""

    def __init__(self, stream: Any) -> None:
        self._stream = stream

    def text(self) -> str:
        return str(self._stream.getvalue())

    def lines(self) -> list[dict[str, Any]]:
        """Every non-empty line parsed as JSON (a line that is not JSON raises)."""
        return [json.loads(line) for line in self.text().splitlines() if line.strip()]


_LOGGERS_TOUCHED = ("", "dbos", "uvicorn", "uvicorn.error", "uvicorn.access")


@pytest.fixture
def capture_json_logs() -> Iterator[JsonLogs]:
    """`configure_logging` pointed at an in-memory stream; the stdlib and structlog state it
    changes is restored afterwards."""
    import io  # noqa: PLC0415
    import logging  # noqa: PLC0415

    import structlog  # noqa: PLC0415

    from tumnis.core import logging as tumnis_logging  # noqa: PLC0415

    saved = {
        name: (logging.getLogger(name), list(logging.getLogger(name).handlers))
        for name in _LOGGERS_TOUCHED
    }
    state = {name: (lg.level, lg.propagate) for name, (lg, _) in saved.items()}
    stream = io.StringIO()
    tumnis_logging.configure_logging(stream=stream)
    try:
        yield JsonLogs(stream)
    finally:
        for name, (logger, handlers) in saved.items():
            logger.handlers[:] = handlers
            logger.setLevel(state[name][0])
            logger.propagate = state[name][1]
        structlog.reset_defaults()
