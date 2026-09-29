"""The app role owns nothing and bypasses nothing (P0-06, ADR-0009)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
import pytest
from psycopg import errors as pg_errors

from tests._pg import APP, OWNER

if TYPE_CHECKING:
    from tests._pg import DbUrls

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("ADR-0009")
@pytest.mark.wp("P0-06")
@pytest.mark.xfail(strict=True, reason="spec:P0-06")
def test_app_role_has_no_bypass_and_owns_nothing(db: DbUrls) -> None:
    """T-P0-06-05
    `rolbypassrls`, `rolsuper`, `rolcreatedb`, `rolcreaterole` are false; zero tables,
    functions or schemas are owned by `tumnis_app` in the app database; it is not a member
    of `tumnis_owner`.
    """
    with psycopg.connect(db.libpq(OWNER)) as conn:
        flags = conn.execute(
            "SELECT rolbypassrls, rolsuper, rolcreatedb, rolcreaterole, rolinherit "
            "FROM pg_roles WHERE rolname = %s",
            (APP,),
        ).fetchone()
        assert flags == (False, False, False, False, False)

        owned = conn.execute(
            """
            SELECT 'relation', c.relname FROM pg_class c
              JOIN pg_roles r ON r.oid = c.relowner WHERE r.rolname = %(app)s
            UNION ALL
            SELECT 'function', p.proname FROM pg_proc p
              JOIN pg_roles r ON r.oid = p.proowner WHERE r.rolname = %(app)s
            UNION ALL
            SELECT 'schema', n.nspname FROM pg_namespace n
              JOIN pg_roles r ON r.oid = n.nspowner WHERE r.rolname = %(app)s
            """,
            {"app": APP},
        ).fetchall()
        assert owned == []

        member = conn.execute(
            "SELECT pg_has_role(%s, %s, 'MEMBER'), pg_has_role(%s, %s, 'USAGE')",
            (APP, OWNER, APP, OWNER),
        ).fetchone()
        assert member == (False, False)

        # The app database, schema app and the tenancy helpers are what the app role gets.
        assert conn.execute(
            "SELECT has_schema_privilege(%s, 'app', 'USAGE'), "
            "has_schema_privilege(%s, 'public', 'CREATE'), "
            "has_function_privilege(%s, 'app.current_workspace_id()', 'EXECUTE')",
            (APP, APP, APP),
        ).fetchone() == (True, False, True)


@pytest.mark.req("ADR-0009")
@pytest.mark.wp("P0-06")
@pytest.mark.xfail(strict=True, reason="spec:P0-06")
@pytest.mark.parametrize(
    "statement",
    [
        "TRUNCATE workspaces",
        "ALTER TABLE workspaces ADD COLUMN sneaky text",
        "ALTER TABLE workspaces DISABLE ROW LEVEL SECURITY",
        "DROP POLICY tenant_isolation ON workspaces",
    ],
)
def test_app_role_cannot_truncate_or_alter(db: DbUrls, statement: str) -> None:
    """T-P0-06-06
    `TRUNCATE workspaces`, `ALTER TABLE` and `DROP POLICY` as the app role raise
    `InsufficientPrivilege`.
    """
    with (
        psycopg.connect(db.libpq(APP)) as conn,
        pytest.raises(pg_errors.InsufficientPrivilege),
    ):
        conn.execute(statement)
