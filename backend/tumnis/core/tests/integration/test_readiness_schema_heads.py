"""Bug: readiness `schema` read `behind` on a freshly migrated database (P0-30 x P0-12).

Module branches depend on each other through `depends_on` (P0-06): `calendar_0001` and
`knowledge_0001` depend on `integrations_0001`. `ScriptDirectory.get_heads()` lists
`integrations_0001` as a head (no revision names it as `down_revision`), but after
`upgrade heads` Alembic keeps only the dependents in `alembic_version`, so comparing the
version rows to the script heads read `behind` and the api never went ready.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import TYPE_CHECKING

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    import httpx

    from tests._pg import DbUrls

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.fixture
def empty_db(pg_base: DbUrls) -> Iterator[DbUrls]:
    """A new database with no revisions applied, prepared like `tumnis` (02-database.sql)."""
    from tests._pg import create_database, drop  # noqa: PLC0415

    name = f"t_{uuid.uuid4().hex[:12]}"
    urls = create_database(pg_base, name)
    try:
        yield urls
    finally:
        drop(pg_base, name)


def _versions(db: DbUrls) -> set[str]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        rows = conn.execute("SELECT version_num FROM alembic_version").fetchall()
    return {version for (version,) in rows}


@pytest.mark.req("REL-4", "REL-5")
@pytest.mark.wp("P0-30")
def test_readiness_schema_at_head_with_dependent_branches(empty_db: DbUrls) -> None:
    """A fresh database upgraded to heads with this release's real migration tree (module
    branches joined by `depends_on`) is at head: `migrate --check` passes and the position
    readiness asks for is at_head. Downgrading one branch makes it behind again."""
    from alembic import command  # noqa: PLC0415
    from alembic.script import ScriptDirectory  # noqa: PLC0415

    from tumnis.migrate import (  # noqa: PLC0415
        ALEMBIC_INI,
        DbPosition,
        MigrationPendingError,
        alembic_config,
        release_revisions,
        verify_at_heads,
    )

    cfg = alembic_config(ALEMBIC_INI, empty_db.owner)
    script = ScriptDirectory.from_config(cfg)
    # The shape that triggered the bug: a script head another branch depends on.
    depended_on = {
        dep.revision
        for rev in script.walk_revisions()
        for dep in script.get_revisions(rev.dependencies or ())
        if dep is not None
    }
    assert set(script.get_heads()) & depended_on, "the tree no longer has a depended-on head"

    command.upgrade(cfg, "heads")
    current = _versions(empty_db)
    assert release_revisions().position(current) is DbPosition.AT_HEAD, sorted(current)
    verify_at_heads(ALEMBIC_INI, empty_db.owner)

    # Behind: one head's last revision undone (a head reached only by down_revision).
    previous = next(
        down
        for h in sorted(script.get_heads())
        if isinstance(down := script.get_revision(h).down_revision, str) and h not in depended_on
    )
    command.downgrade(cfg, previous)
    behind = _versions(empty_db)
    assert release_revisions().position(behind) is DbPosition.BEHIND, sorted(behind)
    with pytest.raises(MigrationPendingError):
        verify_at_heads(ALEMBIC_INI, empty_db.owner)
    assert release_revisions().position(set()) is DbPosition.BEHIND


@pytest.mark.req("REL-4", "REL-5")
@pytest.mark.wp("P0-30")
async def test_readiness_schema_ok_on_migrated_database(
    db: DbUrls, client: httpx.AsyncClient, dbos: object
) -> None:
    """/health/ready on a database at this release's heads is 200 with `schema` ok. The
    test template also carries the harness's test-only revisions, which this release does
    not know (they read as ahead and hid the bug); without them the version rows are what
    `tumnis migrate` leaves."""
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        conn.execute("DELETE FROM alembic_version WHERE version_num LIKE 'harness_%%'")
    response = await client.get("/health/ready")
    assert response.json()["checks"]["schema"] == "ok", response.text
    assert response.status_code == 200, response.text
