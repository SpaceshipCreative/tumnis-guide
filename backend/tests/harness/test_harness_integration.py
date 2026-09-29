"""Harness self tests, integration layer: Postgres clones, DBOS, seed CLI, services (P0-02)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from dbos import DBOS
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tests._pg import DbUrls
    from tests._services import ClamdEndpoint, S3Endpoint, SftpEndpoint
    from tests.fixtures import QueryCounter

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

ANCHOR = date(2026, 3, 9)

# Databases handed out so far on this xdist worker; each test must get a new one.
_SEEN_DATABASES: set[str] = set()


# --- T-P0-02-10: per-test database cloned from the template ---------------------------


@pytest.mark.req("Quality rule 5")
@pytest.mark.wp("P0-02")
@pytest.mark.parametrize("run", ["first", "second"])
async def test_db_is_fresh_clone_with_roles(  # noqa: PLR0917
    run: str,
    db: DbUrls,
    pg_base: DbUrls,
    db_template: str,
    owner_session: AsyncSession,
    app_role_session: AsyncSession,
) -> None:
    """T-P0-02-10
    Each db is a new database cloned from the template; both roles can connect; a row
    written in one test is absent in the next.
    """
    from tests._pg import APP, OWNER, clone, drop  # noqa: PLC0415

    assert db.name.startswith("t_")
    assert db.name not in _SEEN_DATABASES
    _SEEN_DATABASES.add(db.name)

    for session, role in ((owner_session, OWNER), (app_role_session, APP)):
        row = (await session.execute(text("SELECT current_database(), current_user"))).one()
        assert tuple(row) == (db.name, role)

    probe_count = text("SELECT count(*) FROM harness_probe")
    assert (await app_role_session.execute(probe_count)).scalar_one() == 0
    await app_role_session.execute(
        text("INSERT INTO harness_probe (note) VALUES (:note)"), {"note": run}
    )
    await app_role_session.commit()
    assert (await app_role_session.execute(probe_count)).scalar_one() == 1

    # The row never reaches the template: a fresh clone starts empty again.
    other = clone(pg_base, db_template, f"t_{uuid.uuid4().hex[:12]}")
    try:
        with psycopg.connect(other.libpq(APP)) as conn:
            assert conn.execute("SELECT count(*) FROM harness_probe").fetchone() == (0,)
    finally:
        drop(pg_base, other.name)


@pytest.mark.req("Quality rule 5")
@pytest.mark.wp("P0-02")
async def test_query_counter_counts_statements(
    owner_session: AsyncSession, query_counter: QueryCounter
) -> None:
    """The query_counter fixture counts the statements an engine sends."""
    await owner_session.execute(text("SELECT 0"))  # connect first: skip dialect setup queries
    query_counter.watch(owner_session.bind)
    await owner_session.execute(text("SELECT 1"))
    await owner_session.execute(text("SELECT 2"))
    assert query_counter.count == 2
    assert query_counter.statements == ["SELECT 1", "SELECT 2"]
    query_counter.reset()
    assert query_counter.count == 0


# --- T-P0-02-11: DBOS on the test Postgres --------------------------------------------


@DBOS.step()
def _harness_add_one(value: int) -> int:
    return value + 1


@DBOS.workflow()
def harness_toy_workflow(value: int) -> int:
    return _harness_add_one(_harness_add_one(value))


@pytest.mark.req("Quality rule 5")
@pytest.mark.wp("P0-02")
@pytest.mark.parametrize("run", ["first", "second"])
def test_dbos_fixture_runs_a_workflow_on_postgres(run: str, dbos: type[DBOS]) -> None:
    """T-P0-02-11
    A toy workflow with two steps runs and its status is SUCCESS; each test starts with an
    empty workflow table (the truncate reset works).
    """
    assert dbos.list_workflows() == [], f"{run}: workflows left over from an earlier test"

    handle = dbos.start_workflow(harness_toy_workflow, 1)
    assert handle.get_result() == 3
    status = dbos.retrieve_workflow(handle.workflow_id).get_status()
    assert status.status == "SUCCESS"
    assert len(dbos.list_workflow_steps(handle.workflow_id)) == 2
    assert [w.workflow_id for w in dbos.list_workflows()] == [handle.workflow_id]


# --- T-P0-02-03: `tumnis seed` into Postgres ------------------------------------------

_SEED_TABLES = {"projects": "name", "tasks": "title", "events": "title"}
_VOLATILE_COLUMNS = {"created_at", "updated_at"}


def _natural_rows(conn: psycopg.Connection[Any]) -> dict[str, list[tuple[Any, ...]]]:
    """Rows per table with UUID columns dropped and FK UUIDs replaced by the parent's
    natural key (projects by name, tasks by title, events by title)."""
    natural: dict[Any, str] = {}
    for table, key in _SEED_TABLES.items():
        for row_id, value in conn.execute(f"SELECT id, {key} FROM {table}"):  # noqa: S608
            natural[row_id] = value
    rows: dict[str, list[tuple[Any, ...]]] = {}
    for table in _SEED_TABLES:
        cursor = conn.execute(f"SELECT * FROM {table}")  # noqa: S608
        names = [col.name for col in cursor.description or ()]
        out = []
        for record in cursor:
            kept = []
            for name, value in zip(names, record, strict=True):
                if name == "id" or name in _VOLATILE_COLUMNS:
                    continue
                if isinstance(value, uuid.UUID):
                    if value not in natural:
                        continue  # workspace ids and other non-seeded references
                    value = natural[value]  # noqa: PLW2901
                kept.append((name, value))
            out.append(tuple(kept))
        rows[table] = sorted(out, key=repr)
    return rows


@pytest.fixture
def second_db(pg_base: DbUrls, db_template: str) -> Iterator[DbUrls]:
    from tests._pg import clone, drop  # noqa: PLC0415

    name = f"t_{uuid.uuid4().hex[:12]}"
    urls = clone(pg_base, db_template, name)
    try:
        yield urls
    finally:
        drop(pg_base, name)


@pytest.mark.req("Quality rule 5")
@pytest.mark.wp("P0-02")
def test_tumnis_seed_loads_into_postgres(
    db: DbUrls, second_db: DbUrls, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-P0-02-03
    `tumnis seed` against db gives 3 projects, 30 tasks and one day of events; a second
    database gives identical rows apart from IDs.
    """
    from typer.testing import CliRunner  # noqa: PLC0415

    from tests._pg import OWNER  # noqa: PLC0415
    from tumnis import cli  # noqa: PLC0415
    from tumnis.core.clock import FixedClock  # noqa: PLC0415

    monkeypatch.setattr(cli, "make_clock", lambda: FixedClock(datetime(2026, 3, 9, 12, tzinfo=UTC)))
    snapshots = []
    for target in (db, second_db):
        monkeypatch.setenv("DATABASE_URL", target.app)
        monkeypatch.setenv("DATABASE_OWNER_URL", target.owner)
        result = CliRunner().invoke(cli.app, ["seed", "--set", "seed", "--anchor", "2026-03-09"])
        assert result.exit_code == 0, result.output
        with psycopg.connect(target.libpq(OWNER)) as conn:
            counts = {
                table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
                for table in _SEED_TABLES
            }
            assert counts == {"projects": (3,), "tasks": (30,), "events": (6,)}
            days = conn.execute(
                "SELECT DISTINCT (start_at AT TIME ZONE 'America/New_York')::date FROM events"
            ).fetchall()
            assert days == [(ANCHOR,)]
            snapshots.append(_natural_rows(conn))
    assert snapshots[0] == snapshots[1]


# --- T-P0-02-17: disposable service containers ----------------------------------------

EICAR = "X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


async def _check_minio(endpoint: S3Endpoint) -> None:
    import aioboto3  # noqa: PLC0415

    bucket = f"harness-{uuid.uuid4().hex[:8]}"
    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=endpoint.url,
        aws_access_key_id=endpoint.access_key,
        aws_secret_access_key=endpoint.secret_key,
        region_name=endpoint.region,
    ) as s3:
        await s3.create_bucket(Bucket=bucket)
        listed = await s3.list_buckets()
    assert bucket in {b["Name"] for b in listed["Buckets"]}


async def _check_sftp(endpoint: SftpEndpoint) -> None:
    import asyncssh  # noqa: PLC0415

    known_hosts = asyncssh.import_known_hosts(
        f"[{endpoint.host}]:{endpoint.port} {endpoint.host_key}\n"
    )
    async with (
        asyncssh.connect(
            endpoint.host,
            endpoint.port,
            username=endpoint.user,
            client_keys=[str(endpoint.private_key_path)],
            known_hosts=known_hosts,
        ) as conn,
        conn.start_sftp_client() as sftp,
    ):
        name = f"upload/probe-{uuid.uuid4().hex[:8]}.txt"
        async with sftp.open(name, "w") as handle:
            await handle.write("probe")
        listed = await sftp.listdir("upload")
    assert name.removeprefix("upload/") in listed


async def _clamd_command(endpoint: ClamdEndpoint, payload: bytes) -> bytes:
    reader, writer = await asyncio.open_connection(endpoint.host, endpoint.port)
    try:
        writer.write(payload)
        await writer.drain()
        return await asyncio.wait_for(reader.read(), timeout=30)
    finally:
        writer.close()
        await writer.wait_closed()


async def _check_clamd(endpoint: ClamdEndpoint) -> None:
    assert await _clamd_command(endpoint, b"zPING\0") == b"PONG\0"
    body = EICAR.encode()
    stream = b"zINSTREAM\0" + len(body).to_bytes(4, "big") + body + (0).to_bytes(4, "big")
    reply = await _clamd_command(endpoint, stream)
    assert b"FOUND" in reply, reply
    assert b"EICAR" in reply.upper(), reply


_CHECKS: dict[str, Callable[[Any], Awaitable[None]]] = {
    "minio": _check_minio,
    "sftp_server": _check_sftp,
    "clamd": _check_clamd,
}


@pytest.mark.req("Quality rule 5")
@pytest.mark.wp("P0-02")
@pytest.mark.parametrize(
    "service", ["minio", "sftp_server", pytest.param("clamd", marks=pytest.mark.slow)]
)
async def test_service_fixtures_answer(service: str, request: pytest.FixtureRequest) -> None:
    """T-P0-02-17
    minio: create and list a bucket with aioboto3; sftp_server: asyncssh connects with the
    key and pinned host key and lists upload/; clamd: PING returns PONG and EICAR is flagged.
    """
    endpoint = request.getfixturevalue(service)
    await _CHECKS[service](endpoint)
