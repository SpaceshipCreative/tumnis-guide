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
@pytest.mark.xfail(strict=True, reason="spec:P0-15")
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
@pytest.mark.xfail(strict=True, reason="spec:P0-15")
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
