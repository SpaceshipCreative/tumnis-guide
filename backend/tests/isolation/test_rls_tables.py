"""Per-table row-level security (A0.3's table half, P0-06, ADR-0009).

The table list comes from the migrations themselves (Alembic's offline SQL, read at
collection), so a table a later revision creates is parametrized here without anyone
listing it; `test_isolation_covers_every_catalog_table` cross-checks it against the live
catalog of the migrated database.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from tests._pg import OWNER

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _declared_tables() -> list[str]:
    from tests.meta._catalog import declared_fenced_tables  # noqa: PLC0415

    return declared_fenced_tables()


@pytest.fixture
async def core_db(db: DbUrls) -> AsyncIterator[None]:
    from tumnis.core import db as core_db  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    try:
        yield
    finally:
        await core_db.dispose()


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@pytest.mark.req("Hosted readiness", "ADR-0009")
@pytest.mark.wp("P0-06")
@pytest.mark.xfail(strict=True, reason="spec:P0-06")
@pytest.mark.parametrize("table", _declared_tables())
@pytest.mark.usefixtures("core_db")
async def test_isolation(
    table: str, db: DbUrls, two_workspaces: tuple[WorkspaceHandle, WorkspaceHandle]
) -> None:
    """T-P0-06-13
    As B: A's rows are invisible, updates and deletes affect 0 rows, an insert with A's
    `workspace_id` fails the policy check; with no context: 0 rows.
    """
    from tests.meta._catalog import has_column, tenant_key  # noqa: PLC0415
    from tumnis.core import db as core_db  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.tests.integration.row_factory import minimal_row  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415

    a, b = two_workspaces
    tbl = _quoted(table)
    with psycopg.connect(db.libpq(OWNER)) as owner:
        key = tenant_key(owner, table)
        touched = "version" if has_column(owner, table, "version") else key
        row: dict[str, Any] = minimal_row(owner, table, a.id)
        owner.commit()  # keeps any FK parents minimal_row made in A
        assert owner.execute(
            f"SELECT count(*) FROM {tbl} WHERE {key} = %s",  # noqa: S608  # catalog names
            (a.id,),
        ).fetchone() != (0,), f"{table}: A has no rows, the check would pass vacuously"
    if key == "workspace_id":
        row["workspace_id"] = a.id

    as_b = WorkspaceContext(b.id, SYSTEM_ACTOR)
    where_a = {"a": a.id}
    async with tenant_session(as_b) as s:
        seen = await s.execute(text(f"SELECT count(*) FROM {tbl} WHERE {key} = :a"), where_a)  # noqa: S608
        assert seen.scalar_one() == 0
        updated = await s.execute(
            text(f"UPDATE {tbl} SET {touched} = {touched} WHERE {key} = :a"),  # noqa: S608
            where_a,
        )
        assert updated.rowcount == 0  # type: ignore[attr-defined]
        deleted = await s.execute(text(f"DELETE FROM {tbl} WHERE {key} = :a"), where_a)  # noqa: S608
        assert deleted.rowcount == 0  # type: ignore[attr-defined]

    columns = ", ".join(_quoted(c) for c in row)
    values = ", ".join(f":{c}" for c in row)
    with pytest.raises(ProgrammingError) as refused:
        async with tenant_session(as_b) as s:
            await s.execute(text(f"INSERT INTO {tbl} ({columns}) VALUES ({values})"), row)  # noqa: S608
    assert isinstance(refused.value.orig, psycopg.errors.InsufficientPrivilege)
    assert "row-level security" in str(refused.value.orig)

    async with core_db.app_sessionmaker()() as s, s.begin():
        assert (await s.execute(text(f"SELECT count(*) FROM {tbl}"))).scalar_one() == 0  # noqa: S608


@pytest.mark.req("ADR-0009")
@pytest.mark.wp("P0-06")
@pytest.mark.xfail(strict=True, reason="spec:P0-06")
def test_every_tenant_table_has_rows_in_both_workspaces(
    db: DbUrls, two_workspaces: tuple[WorkspaceHandle, WorkspaceHandle]
) -> None:
    """T-P0-06-14
    `two_workspaces` leaves at least one row per tenant table in A and in B, so no table
    passes T-P0-06-13 vacuously.
    """
    from tests.meta._catalog import fenced_tables, tenant_key  # noqa: PLC0415

    a, b = two_workspaces
    empty = []
    with psycopg.connect(db.libpq(OWNER)) as conn:
        tables = fenced_tables(conn)
        assert "workspaces" in tables
        for table in tables:
            key = tenant_key(conn, table)
            for ws in (a, b):
                row = conn.execute(
                    f"SELECT count(*) FROM {_quoted(table)} WHERE {key} = %s",  # noqa: S608
                    (ws.id,),
                ).fetchone()
                if row == (0,):
                    empty.append(f"{table} in {ws.name}")
    assert empty == []


@pytest.mark.req("ADR-0009")
@pytest.mark.wp("P0-06")
def test_isolation_covers_every_catalog_table(db: DbUrls) -> None:
    """The tables T-P0-06-13 is parametrized with (read from the migrations at collection)
    are exactly the fenced tables in the migrated database's catalog."""
    from tests.meta._catalog import fenced_tables  # noqa: PLC0415

    with psycopg.connect(db.libpq(OWNER)) as conn:
        assert sorted(_declared_tables()) == sorted(fenced_tables(conn))
