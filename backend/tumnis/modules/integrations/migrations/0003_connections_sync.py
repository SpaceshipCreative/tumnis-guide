"""Connections and the sync framework (P3-02), expand only.

- connections: `account_label` (what the user calls the account), `settings` (backfill
  days, cadence, allow-list; never credentials), `status_detail` (the sentence Settings
  shows), `last_success_at`, `next_sync_at` (NULL: due now), `consent_ack_at` (a
  provider's consent notice was acknowledged) and `failures` (syncs failed in a row, for
  the backoff).
- sync_state: one cursor per (connection, scope) instead of one per connection, with the
  page count of the running sync (`page_no`). The new unique index is added here; the old
  per-connection one goes in `integrations_0004` (contract), so a release still running
  the old code keeps writing one row per connection meanwhile.
- oauth_pending: the connection a consent is for, the `connect_oauth` workflow waiting on
  it, and the issuer the callback reported (RFC 9207).
- app.connector_sync_ages(): for /metrics, per provider the oldest age in seconds since a
  live connection last synced well (or was created), and the items its syncs have seen.
  The scrape reads as the app role with no workspace context, which row-level security
  shows no connections; this SECURITY DEFINER function, owned by tumnis_owner, returns one
  row per provider and nothing else (no ids, no workspaces, no accounts). Pinned by
  tests/meta/test_security_definer.py.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "integrations_0003"
down_revision = "integrations_0002"
branch_labels = None
depends_on = None
phase = "expand"

TZ = sa.TIMESTAMP(timezone=True)

FUNCTIONS = (
    """
    CREATE FUNCTION app.connector_sync_ages()
      RETURNS TABLE (provider text, age_seconds double precision, items bigint)
      LANGUAGE sql STABLE SECURITY DEFINER
      SET search_path = pg_catalog, public
      AS $$
        SELECT c.provider,
               max(extract(epoch FROM now() - coalesce(c.last_success_at, c.created_at)))::float8,
               coalesce(sum(s.items), 0)::bigint
          FROM public.connections AS c
          LEFT JOIN (
            SELECT connection_id, sum(items_seen) AS items FROM public.sync_state
             GROUP BY connection_id
          ) AS s ON s.connection_id = c.id
         WHERE c.deleted_at IS NULL AND c.status <> 'disabled'
         GROUP BY c.provider
      $$
    """,
    "REVOKE ALL ON FUNCTION app.connector_sync_ages() FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION app.connector_sync_ages() TO tumnis_app",
)


def upgrade() -> None:
    op.add_column(
        "connections",
        sa.Column("account_label", sa.Text, nullable=False, server_default=sa.text("''")),
    )
    op.add_column(
        "connections",
        sa.Column("settings", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column("connections", sa.Column("status_detail", sa.Text, nullable=True))
    op.add_column("connections", sa.Column("last_success_at", TZ, nullable=True))
    op.add_column("connections", sa.Column("next_sync_at", TZ, nullable=True))
    op.add_column("connections", sa.Column("consent_ack_at", TZ, nullable=True))
    op.add_column(
        "connections",
        sa.Column("failures", sa.Integer, nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "sync_state",
        sa.Column("scope", sa.Text, nullable=False, server_default=sa.text("'default'")),
    )
    op.add_column(
        "sync_state",
        sa.Column("page_no", sa.Integer, nullable=False, server_default=sa.text("0")),
    )
    op.create_index(
        "uq_sync_state_ws_connection_scope",
        "sync_state",
        ["workspace_id", "connection_id", "scope"],
        unique=True,
    )
    # No foreign key: adding one scans and locks both tables (squawk); a pending consent
    # names its connection only for the callback, and the row dies within ten minutes.
    op.add_column("oauth_pending", sa.Column("connection_id", UUID(as_uuid=True), nullable=True))
    op.add_column("oauth_pending", sa.Column("workflow_id", sa.Text, nullable=True))
    op.add_column("oauth_pending", sa.Column("iss", sa.Text, nullable=True))
    for statement in FUNCTIONS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP FUNCTION app.connector_sync_ages()")
    op.drop_column("oauth_pending", "iss")
    op.drop_column("oauth_pending", "workflow_id")
    op.drop_column("oauth_pending", "connection_id")
    op.drop_index("uq_sync_state_ws_connection_scope", table_name="sync_state")
    op.drop_column("sync_state", "page_no")
    op.drop_column("sync_state", "scope")
    for column in (
        "failures",
        "consent_ack_at",
        "next_sync_at",
        "last_success_at",
        "status_detail",
        "settings",
        "account_label",
    ):
        op.drop_column("connections", column)
