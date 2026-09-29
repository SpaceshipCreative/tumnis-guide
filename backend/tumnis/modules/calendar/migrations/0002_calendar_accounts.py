"""Google accounts and the calendar each event came from (P1-09, R-15).

- `calendar_accounts`: one row per connected Google account (its `connections` row holds
  the sealed tokens): the account's address, its calendar list (`calendars`, for Settings),
  the calendars chosen for sync, `status` (`connected` or `needs_reauth` after a revoked
  grant), the last completed sync and `sync_owner` (the sync workflow holding the account,
  so a manual and a scheduled sync never run at once).
- `events.calendar_id`: the Google calendar an event was read from, so deselecting a
  calendar can drop its events. `all_day` and `provider_url` already exist
  (`calendar_0001` and `canonical_columns`).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "calendar_0002"
down_revision = "calendar_0001"
branch_labels = None
depends_on = None
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "calendar_accounts",
        sa.Column(
            "connection_id", UUID(as_uuid=True), sa.ForeignKey("connections.id"), nullable=False
        ),
        sa.Column("google_email", sa.Text, nullable=False),
        sa.Column("calendars", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "selected_calendar_ids",
            ARRAY(sa.Text),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'connected'")),
        sa.Column("last_sync_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("sync_owner", sa.Text, nullable=True),
        sa.CheckConstraint(
            "status IN ('connected', 'needs_reauth')", name="ck_calendar_accounts_status"
        ),
        sa.Index(
            "uq_calendar_accounts_ws_connection", "workspace_id", "connection_id", unique=True
        ),
    )
    op.add_column("events", sa.Column("calendar_id", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("events", "calendar_id")
    drop_tenant_table("calendar_accounts")
