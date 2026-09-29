"""app.dead_letter_counts(): dead letters by status across workspaces, for /metrics (P0-27).

The scrape reads as the owner-free app role with no workspace context, which row-level
security shows no dead_letters rows. This SECURITY DEFINER function, owned by
tumnis_owner, returns only a count per status (no IDs, no workspaces, no payloads); it is
pinned by tests/meta/test_security_definer.py.
"""

from alembic import op

revision = "core_p027_metrics"
down_revision = "core_0005_settings"
branch_labels = None
depends_on = None
phase = "expand"

FUNCTIONS = (
    """
    CREATE FUNCTION app.dead_letter_counts()
      RETURNS TABLE (status text, n bigint)
      LANGUAGE sql STABLE SECURITY DEFINER
      SET search_path = pg_catalog, public
      AS $$
        SELECT d.status, count(*) FROM public.dead_letters AS d
        WHERE d.deleted_at IS NULL GROUP BY d.status
      $$
    """,
    "REVOKE ALL ON FUNCTION app.dead_letter_counts() FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION app.dead_letter_counts() TO tumnis_app",
)


def upgrade() -> None:
    for statement in FUNCTIONS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP FUNCTION app.dead_letter_counts()")
