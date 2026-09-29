"""Liveness and readiness (P0-04): /health/live does no I/O; /health/ready aggregates checks."""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any

import httpx
import psycopg
import pytest
from psycopg import sql

from tests._pg import APP, DbUrls
from tests.fixtures import settings_for

if TYPE_CHECKING:
    from testcontainers.community.postgres import PostgresContainer

    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

# Nothing listens on port 1: every connection is refused at once.
CLOSED = "postgresql+psycopg://x:y@127.0.0.1:1/none"


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")


def _app_without_postgres(clock: FixedClock) -> Any:
    from tumnis.app import create_app  # noqa: PLC0415
    from tumnis.settings import Settings  # noqa: PLC0415

    settings = Settings(
        database_url=CLOSED,
        database_direct_url=CLOSED,
        dbos_system_database_url=CLOSED,
        tumnis_adapters="fake",
    )
    return create_app(settings=settings, clock=clock)


@pytest.mark.req("REL-5")
@pytest.mark.wp("P0-04")
async def test_live_is_200_while_postgres_is_down(clock: FixedClock) -> None:
    """T-P0-04-01
    The app starts with Postgres unreachable, and liveness answers 200 without any I/O.
    """
    async with _client(_app_without_postgres(clock)) as client:
        response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.req("REL-5")
@pytest.mark.wp("P0-04")
async def test_ready_is_503_while_postgres_is_down(clock: FixedClock) -> None:
    """T-P0-04-02
    A critical check failing makes readiness 503 with checks.postgres = "down".
    """
    async with _client(_app_without_postgres(clock)) as client:
        response = await client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "down"
    assert body["checks"]["postgres"] == "down"


@pytest.mark.req("REL-5")
@pytest.mark.wp("P0-04")
async def test_ready_reports_degraded_module_and_stays_200(
    client: httpx.AsyncClient, dbos: object
) -> None:
    """T-P0-04-03
    A failing non-critical module check gives 200, status degraded, that module degraded,
    every other check ok.
    """
    from tumnis.core.health import register_health  # noqa: PLC0415

    async def failing() -> Any:
        raise RuntimeError("calendar provider unreachable")

    register_health("module:calendar", failing, critical=False)
    response = await client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["module:calendar"] == "degraded"
    others = {name: state for name, state in body["checks"].items() if name != "module:calendar"}
    assert {"postgres", "dbos"} <= others.keys()
    assert set(others.values()) == {"ok"}


@pytest.mark.req("REL-5")
@pytest.mark.wp("P0-04")
@pytest.mark.xfail(strict=True, reason="spec:P0-04")
async def test_ready_checks_dbos_system_tables(
    db: DbUrls, pg_base: DbUrls, pg_container: PostgresContainer, clock: FixedClock
) -> None:
    """T-P0-04-04
    Given a DBOS system database with its tables, ready is 200; after dropping that
    database, ready is 503 with checks.dbos = "down".
    """
    from dbos.cli.migration import run_dbos_database_migrations  # noqa: PLC0415

    from tumnis.app import create_app  # noqa: PLC0415
    from tumnis.core import db as core_db  # noqa: PLC0415

    name = f"t_dbos_{uuid.uuid4().hex[:12]}"
    superuser = pg_container.get_connection_url()
    with psycopg.connect(superuser, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name), sql.Identifier(APP))
        )
    dbos_db = DbUrls(pg_base.host, pg_base.port, name)
    try:
        await asyncio.to_thread(run_dbos_database_migrations, dbos_db.url(APP))
        app = create_app(settings=settings_for(db, dbos_db), clock=clock)
        async with _client(app) as client:
            before = await client.get("/health/ready")
            assert before.status_code == 200
            assert before.json()["checks"]["dbos"] == "ok"

            with psycopg.connect(superuser, autocommit=True) as conn:
                conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
            after = await client.get("/health/ready")
        assert after.status_code == 503
        assert after.json()["checks"]["dbos"] == "down"
    finally:
        await core_db.dispose()
        with psycopg.connect(superuser, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )
