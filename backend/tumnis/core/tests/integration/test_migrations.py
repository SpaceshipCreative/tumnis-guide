"""Migrations run forward and back on empty and on filled databases, and squawk guards the
expand revisions (P0-06, REL-4)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    from testcontainers.community.postgres import PostgresContainer

    from tests._pg import DbUrls

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

FIXTURE_MIGRATIONS = Path(__file__).parent / "fixtures" / "migrations"


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


@pytest.mark.req("REL-4")
@pytest.mark.wp("P0-06")
@pytest.mark.xfail(strict=True, reason="spec:P0-06")
def test_upgrade_downgrade_upgrade_empty(pg_container: PostgresContainer, empty_db: DbUrls) -> None:
    """T-P0-06-10
    `upgrade heads`, `downgrade base`, `upgrade heads` on an empty database; the schema dump
    is equal before and after.
    """
    from alembic import command  # noqa: PLC0415

    from tests._pg import alembic_config, schema_dump  # noqa: PLC0415

    cfg = alembic_config(empty_db.owner)
    command.upgrade(cfg, "heads")
    first = schema_dump(pg_container, empty_db.name)
    assert "CREATE TABLE public.workspaces" in first

    command.downgrade(cfg, "base")
    with psycopg.connect(empty_db.libpq(OWNER)) as conn:
        left = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1"
        ).fetchall()
        assert left == [("alembic_version",)]
        functions = conn.execute(
            "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'app'"
        ).fetchall()
        assert functions == []

    command.upgrade(cfg, "heads")
    assert schema_dump(pg_container, empty_db.name) == first


@pytest.mark.req("REL-4")
@pytest.mark.wp("P0-06")
@pytest.mark.xfail(strict=True, reason="spec:P0-06")
def test_stairway_on_seeded_database(empty_db: DbUrls) -> None:
    """T-P0-06-11
    For each revision in dependency order: upgrade to it, insert rows with `row_factory`
    into every table that exists, downgrade one step (a branch root goes to
    `<label>@base`), upgrade again. No error means every downgrade is valid against data.
    """
    from alembic import command  # noqa: PLC0415
    from alembic.script import ScriptDirectory  # noqa: PLC0415

    from tests._pg import alembic_config  # noqa: PLC0415
    from tumnis.core.tests.integration.row_factory import fill_every_table  # noqa: PLC0415

    cfg = alembic_config(empty_db.owner)
    revisions = list(reversed(list(ScriptDirectory.from_config(cfg).walk_revisions())))
    assert {"core_0001", "auth_0001"} <= {r.revision for r in revisions}

    for rev in revisions:
        command.upgrade(cfg, rev.revision)
        with psycopg.connect(empty_db.libpq(OWNER), autocommit=True) as conn:
            filled = fill_every_table(conn)
        assert filled, f"{rev.revision}: no table received a row"
        if rev.down_revision is None:
            (label,) = rev.branch_labels
            target = f"{label}@base"
        else:
            assert isinstance(rev.down_revision, str), "merge revisions need a stairway rule"
            target = rev.down_revision
        command.downgrade(cfg, target)
        command.upgrade(cfg, rev.revision)


@pytest.mark.req("REL-4")
@pytest.mark.wp("P0-06")
@pytest.mark.xfail(strict=True, reason="spec:P0-06")
def test_squawk_rejects_destructive_expand(tmp_path: Path) -> None:
    """T-P0-06-12
    A fixture expand revision with `op.drop_column` renders SQL that `squawk` rejects; the
    same op in a `contract` revision is skipped by the runner.
    """
    from tests.ci._scripts import load  # noqa: PLC0415

    squawk = load("squawk_migrations")
    bad = FIXTURE_MIGRATIONS / "bad_expand.py"

    results = squawk.check([bad], extra_locations=[FIXTURE_MIGRATIONS])
    assert [(r.revision, r.status) for r in results] == [("bad_expand", "rejected")]
    assert "ban-drop-column" in results[0].output
    assert "DROP COLUMN" in results[0].sql

    contract = tmp_path / "contract"
    contract.mkdir()
    same_op = contract / "bad_contract.py"
    same_op.write_text(bad.read_text().replace('phase = "expand"', 'phase = "contract"'))
    results = squawk.check([same_op], extra_locations=[contract])
    assert [(r.revision, r.status) for r in results] == [("bad_expand", "skipped")]
