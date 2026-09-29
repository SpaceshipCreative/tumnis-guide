"""Core baseline: the tenancy helpers in schema `app` (P0-06, ADR-0009).

`app.current_workspace_id()` and `app.current_actor()` read the transaction-local settings
that tumnis.core.tenancy applies on every transaction; `app.touch_row()` is the update
trigger every tenant table gets from create_tenant_table.
"""

from alembic import op

revision = "core_0002"
down_revision = "core_0001"
branch_labels = None
depends_on = None
phase = "expand"

# NULLIF(..., ''): once a connection has set a custom setting with is_local = true, later
# transactions read it as '' rather than NULL, and ''::uuid would raise instead of
# matching nothing.
UPGRADE = (
    """
    CREATE FUNCTION app.current_workspace_id() RETURNS uuid
      LANGUAGE sql STABLE PARALLEL SAFE
      AS $$ SELECT NULLIF(current_setting('app.workspace_id', true), '')::uuid $$
    """,
    """
    CREATE FUNCTION app.current_actor() RETURNS text
      LANGUAGE sql STABLE PARALLEL SAFE
      AS $$ SELECT COALESCE(NULLIF(current_setting('app.actor', true), ''), 'system') $$
    """,
    """
    CREATE FUNCTION app.touch_row() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      NEW.updated_at := now();
      NEW.version := OLD.version + 1;
      NEW.id := OLD.id;
      NEW.workspace_id := OLD.workspace_id;
      RETURN NEW;
    END $$
    """,
    "GRANT EXECUTE ON FUNCTION app.current_workspace_id(), app.current_actor() TO tumnis_app",
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP FUNCTION app.touch_row()")
    op.execute("DROP FUNCTION app.current_actor()")
    op.execute("DROP FUNCTION app.current_workspace_id()")
