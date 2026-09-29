"""The audit log is append-only (P0-15, SEC-3): no role the app uses can change or remove a
row, and even the owner needs to disable the trigger first."""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
import pytest

from tests._pg import APP, OWNER

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

CHANGES = (
    "UPDATE audit_log SET reason = 'edited'",
    "DELETE FROM audit_log",
    "TRUNCATE audit_log",
)


@pytest.mark.req("SEC-3")
@pytest.mark.wp("P0-15")
def test_app_role_cannot_update_delete_or_truncate(db: DbUrls, workspace: WorkspaceHandle) -> None:
    """T-P0-15-01
    `UPDATE`, `DELETE` and `TRUNCATE audit_log` as the app role, in the row's own
    workspace, raise `InsufficientPrivilege`; the row is still there afterwards.
    """
    from tumnis.core.tests.integration._audit import insert_raw  # noqa: PLC0415

    with psycopg.connect(db.libpq(OWNER), autocommit=True) as owner:
        insert_raw(owner, workspace.id)

    with psycopg.connect(db.libpq(APP)) as app:
        for statement in CHANGES:
            app.execute("SELECT set_config('app.workspace_id', %s, true)", (str(workspace.id),))
            assert app.execute("SELECT count(*) FROM audit_log").fetchone() == (1,)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                app.execute(statement.encode())
            app.rollback()

    with psycopg.connect(db.libpq(OWNER)) as owner:
        assert owner.execute("SELECT count(*) FROM audit_log").fetchone() == (1,)


@pytest.mark.req("SEC-3")
@pytest.mark.wp("P0-15")
def test_trigger_blocks_even_the_owner(db: DbUrls, workspace: WorkspaceHandle) -> None:
    """T-P0-15-02
    The owner's `UPDATE`, `DELETE` and `TRUNCATE` raise from the trigger ("append-only");
    the app role cannot disable the trigger; only `ALTER TABLE ... DISABLE TRIGGER` as the
    owner lets an update through.
    """
    from tumnis.core.tests.integration._audit import TRIGGER, insert_raw  # noqa: PLC0415

    with psycopg.connect(db.libpq(OWNER), autocommit=True) as owner:
        insert_raw(owner, workspace.id)
        for statement in CHANGES:
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"):
                owner.execute(statement.encode())

    with (
        psycopg.connect(db.libpq(APP), autocommit=True) as app,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        app.execute(f"ALTER TABLE audit_log DISABLE TRIGGER {TRIGGER}".encode())

    with psycopg.connect(db.libpq(OWNER)) as owner:
        owner.execute(f"ALTER TABLE audit_log DISABLE TRIGGER {TRIGGER}".encode())
        updated = owner.execute("UPDATE audit_log SET reason = 'edited'")
        assert updated.rowcount == 1
        owner.rollback()


def _append_only() -> list[str]:
    from tests.meta._catalog import ALLOW_LIST  # noqa: PLC0415

    return sorted(name for name, entry in ALLOW_LIST.items() if entry.kind == "append_only")


@pytest.mark.req("SEC-3", "ADR-0009")
@pytest.mark.wp("P0-15")
@pytest.mark.parametrize("table", _append_only())
def test_append_only_tables_are_fenced_per_workspace(
    table: str, db: DbUrls, two_workspaces: tuple[WorkspaceHandle, WorkspaceHandle]
) -> None:
    """The isolation suite's half for the append-only tables (they have no UPDATE or DELETE
    grant to probe): as B, A's rows are invisible and an insert with A's workspace_id fails
    the policy; with no workspace in context, nothing is visible."""
    from psycopg import sql  # noqa: PLC0415

    from tests.meta._catalog import append_only_tables  # noqa: PLC0415
    from tumnis.core.tests.integration.row_factory import insert_row, minimal_row  # noqa: PLC0415

    a, b = two_workspaces
    ident = sql.Identifier(table)
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as owner:
        assert table in append_only_tables(owner)
        row = minimal_row(owner, table, a.id)
        assert insert_row(owner, table, row) is not None

    with psycopg.connect(db.libpq(APP)) as app:
        app.execute("SELECT set_config('app.workspace_id', %s, true)", (str(b.id),))
        seen = app.execute(sql.SQL("SELECT count(*) FROM {}").format(ident)).fetchone()
        assert seen == (0,)
        columns = sql.SQL(", ").join(map(sql.Identifier, row))
        values = sql.SQL(", ").join(sql.Placeholder() * len(row))
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
            app.execute(
                sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(ident, columns, values),
                list(row.values()),
            )
        app.rollback()
        unscoped = app.execute(sql.SQL("SELECT count(*) FROM {}").format(ident)).fetchone()
        assert unscoped == (0,)


@pytest.mark.req("SEC-3", "ADR-0009")
@pytest.mark.wp("P0-15")
def test_registry_flags_an_append_only_table_with_a_change_grant(db: DbUrls) -> None:
    """The registry's append-only rule: granting UPDATE to the app role, or disabling the
    trigger, is reported."""
    from tests.meta._catalog import registry_violations  # noqa: PLC0415

    with psycopg.connect(db.libpq(OWNER), autocommit=True) as owner:
        assert registry_violations(owner) == []
        owner.execute("GRANT UPDATE ON audit_log TO tumnis_app")
        owner.execute("ALTER TABLE audit_anchors DISABLE TRIGGER audit_anchors_immutable")
        found = registry_violations(owner)

    assert found == [
        "rls: audit_anchors: app.audit_immutable() does not guard every change",
        "rls: audit_log: tumnis_app holds UPDATE",
    ], found
