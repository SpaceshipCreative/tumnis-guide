"""deployment_marker behind a SECURITY DEFINER reader (P0-06, A7).

P0-04 let the app role SELECT the marker table; the global tables are reached only through
SECURITY DEFINER functions in schema `app`, so the boot checks now call
`app.deployment_markers()` and the app role loses the table grant.
"""

from alembic import op

revision = "core_0003"
down_revision = "core_0002"
branch_labels = None
depends_on = None
phase = "expand"


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION app.deployment_markers()
          RETURNS TABLE (env text, master_key_fingerprint text)
          LANGUAGE sql STABLE SECURITY DEFINER
          SET search_path = pg_catalog, public
          AS $$ SELECT env, master_key_fingerprint FROM public.deployment_marker $$
        """
    )
    op.execute("GRANT EXECUTE ON FUNCTION app.deployment_markers() TO tumnis_app")
    op.execute("REVOKE SELECT ON deployment_marker FROM tumnis_app")


def downgrade() -> None:
    op.execute("GRANT SELECT ON deployment_marker TO tumnis_app")
    op.execute("DROP FUNCTION app.deployment_markers()")
