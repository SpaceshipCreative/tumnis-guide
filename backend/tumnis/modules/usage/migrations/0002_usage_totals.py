"""app.usage_totals(): usage counters summed per counter across workspaces, for the
`tumnis_usage_total` gauge on /metrics (P0-27).

The scrape reads as the owner-free app role with no workspace context, which row-level
security shows no usage_counters rows. This SECURITY DEFINER function, owned by
tumnis_owner, returns one total per counter name (no days, no workspaces); it is pinned
by tests/meta/test_security_definer.py.
"""

from alembic import op

revision = "usage_0002"
down_revision = "usage_0001"
branch_labels = None
depends_on = None
phase = "expand"

FUNCTIONS = (
    """
    CREATE FUNCTION app.usage_totals()
      RETURNS TABLE (counter text, total bigint)
      LANGUAGE sql STABLE SECURITY DEFINER
      SET search_path = pg_catalog, public
      AS $$
        SELECT u.counter, sum(u.value)::bigint FROM public.usage_counters AS u
        WHERE u.deleted_at IS NULL GROUP BY u.counter
      $$
    """,
    "REVOKE ALL ON FUNCTION app.usage_totals() FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION app.usage_totals() TO tumnis_app",
)


def upgrade() -> None:
    for statement in FUNCTIONS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP FUNCTION app.usage_totals()")
