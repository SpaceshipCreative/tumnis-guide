"""The database backs the code-location rule (P0-17, FR-2.1): a row with both a path and
a repository URL is refused by the check constraint, whoever writes it."""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

INSERT = (
    "INSERT INTO projects (workspace_id, name, status, sort_key, code_path, repo_url)"
    " VALUES (%s, %s, 'active', %s, %s, %s)"
)


@pytest.mark.req("FR-2.1")
@pytest.mark.wp("P0-17")
def test_check_constraint_rejects_both(db: DbUrls, workspace: WorkspaceHandle) -> None:
    """T-P0-17-14
    Inserting a project with both `code_path` and `repo_url` (as the owner, past every
    api check) fails on `ck_projects_one_code_location`; either one alone is stored.
    """
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        conn.execute(INSERT, (workspace.id, "Path", "a0", "/srv/acme", None))
        conn.execute(INSERT, (workspace.id, "Repo", "a1", None, "https://example.com/a.git"))
        with pytest.raises(psycopg.errors.CheckViolation) as refused:
            conn.execute(
                INSERT,
                (workspace.id, "Both", "a2", "/srv/acme", "https://example.com/a.git"),
            )
        assert refused.value.diag.constraint_name == "ck_projects_one_code_location"
        count = conn.execute("SELECT count(*) FROM projects").fetchone()
    assert count == (2,)
