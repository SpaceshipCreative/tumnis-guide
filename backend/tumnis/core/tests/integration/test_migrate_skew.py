"""Version skew (P0-30, REL-4): after a rollback the database is ahead of the running
release. N+1's expand-only schema serves N, so `tumnis migrate` from N exits 0 without
touching the schema, and readiness stays ready."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    import httpx

    from tests._pg import DbUrls

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

# A revision id this release's script directory has never heard of: N+1 added it.
NEXT_RELEASE_REVISION = "zz_next_release_0001"


def _ahead(db: DbUrls) -> None:
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        conn.execute(
            "INSERT INTO alembic_version (version_num) VALUES (%s)", (NEXT_RELEASE_REVISION,)
        )


def _catalog(db: DbUrls) -> tuple[int, list[str]]:
    """How many relations exist, and the alembic_version rows."""
    with psycopg.connect(db.libpq(OWNER)) as conn:
        relations = conn.execute("SELECT count(*) FROM pg_catalog.pg_class").fetchone()
        versions = conn.execute("SELECT version_num FROM alembic_version ORDER BY 1").fetchall()
    assert relations is not None
    return relations[0], [v for (v,) in versions]


@pytest.mark.req("REL-4")
@pytest.mark.wp("P0-30")
def test_migrate_exits_zero_when_database_is_ahead(
    db: DbUrls, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-P0-30-02
    Given the database at head plus a revision this release does not know, `tumnis migrate`
    exits 0, says `database is ahead` and runs no DDL (pg_class count and alembic_version
    unchanged).
    """
    from typer.testing import CliRunner  # noqa: PLC0415

    from tests._pg import alembic_config  # noqa: PLC0415
    from tumnis import cli, migrate  # noqa: PLC0415

    # This release = alembic.ini plus the harness's test-only revisions, which every test
    # template carries: without the insert below the database is exactly at head.
    monkeypatch.setattr(migrate, "alembic_config", lambda _ini, url: alembic_config(url))
    _ahead(db)
    before = _catalog(db)

    env = {
        "DATABASE_URL": db.app,
        "DATABASE_DIRECT_URL": db.app,
        "DATABASE_OWNER_URL": db.owner,
        "DEPLOYMENT_ENV": "dev",
        "TUMNIS_ADAPTERS": "fake",
        "MASTER_KEY_FILE": str(Path("/nonexistent/master_key.json")),
    }
    result = CliRunner().invoke(cli.app, ["migrate"], env=env)

    assert result.exit_code == 0, result.output
    assert "database is ahead" in result.output
    assert _catalog(db) == before


@pytest.mark.req("REL-4")
@pytest.mark.wp("P0-30")
async def test_readiness_is_ready_when_database_is_ahead(
    db: DbUrls, client: httpx.AsyncClient, dbos: object
) -> None:
    """T-P0-30-03
    Given the database ahead of this release (a rollback), /health/ready is 200 and its
    `schema` check is ok.
    """
    _ahead(db)
    response = await client.get("/health/ready")
    assert response.status_code == 200, response.text
    assert response.json()["checks"]["schema"] == "ok"


@pytest.mark.req("REL-4", "REL-5")
@pytest.mark.wp("P0-30")
async def test_readiness_is_down_when_database_is_behind(
    db: DbUrls, client: httpx.AsyncClient, dbos: object
) -> None:
    """A database missing one of this release's heads (migrate has not run) makes the
    critical `schema` check down: readiness 503."""
    from tumnis.migrate import release_revisions  # noqa: PLC0415

    some_head = sorted(release_revisions().heads)[0]
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        # Only this release's revisions stay (the harness's test-only ones are unknown to
        # it and would read as ahead), minus one head.
        conn.execute(
            "DELETE FROM alembic_version WHERE version_num LIKE 'harness_%%' OR version_num = %s",
            (some_head,),
        )
    response = await client.get("/health/ready")
    assert response.status_code == 503, response.text
    assert response.json()["checks"]["schema"] == "down"
