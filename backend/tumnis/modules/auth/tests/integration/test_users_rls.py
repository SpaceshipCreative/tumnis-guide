"""`users` is a global identity table: the app role sees only the signed-in user's row, and
pre-auth lookups go through SECURITY DEFINER functions (P0-13, Hosted readiness)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
import pytest

from tests._pg import APP, OWNER

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("Hosted readiness")
@pytest.mark.wp("P0-13")
@pytest.mark.xfail(strict=True, reason="spec:P0-13")
def test_app_role_reads_only_its_own_user(db: DbUrls, workspace: WorkspaceHandle) -> None:
    """T-P0-13-19
    Two users in two workspaces. As the app role with `app.user_id` set, `SELECT * FROM
    users` returns that user's row only; without it, none, and a lookup by email finds
    nothing; `app.auth_login_lookup(email)` finds either user with its workspace.
    """
    from tests.fixtures import make_workspace  # noqa: PLC0415

    other_ws = make_workspace(db, "Other")
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as owner:
        row = owner.execute(
            "INSERT INTO users (email, password_hash, home_workspace_id) "
            "VALUES ('other@example.test', 'x', %s) RETURNING id",
            (other_ws,),
        ).fetchone()
        assert row is not None
        other_user = row[0]
    assert workspace.user_id is not None
    assert workspace.email is not None

    with psycopg.connect(db.libpq(APP)) as app:
        app.execute("SELECT set_config('app.user_id', %s, true)", (str(workspace.user_id),))
        seen = app.execute("SELECT id, email FROM users").fetchall()
        assert seen == [(workspace.user_id, workspace.email)]
        app.commit()

        assert app.execute("SELECT id FROM users").fetchall() == []
        assert (
            app.execute("SELECT id FROM users WHERE email = 'other@example.test'").fetchall() == []
        )
        found = app.execute(
            "SELECT user_id, workspace_id FROM app.auth_login_lookup('OTHER@example.test')"
        ).fetchall()
        assert found == [(other_user, other_ws)]
        mine = app.execute(
            "SELECT user_id, workspace_id FROM app.auth_login_lookup(%s)", (workspace.email,)
        ).fetchall()
        assert mine == [(workspace.user_id, workspace.id)]
        app.commit()

        # Writing someone else's row is refused by the policy.
        app.execute("SELECT set_config('app.user_id', %s, true)", (str(workspace.user_id),))
        updated = app.execute(
            "UPDATE users SET password_hash = 'y' WHERE id = %s", (other_user,)
        ).rowcount
        assert updated == 0
