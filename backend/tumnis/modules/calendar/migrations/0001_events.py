"""The canonical `events` table (P0-12, R-15): calendar owns it, P1-09 extends it in
`calendar_0002`. `connection_id` and `raw_payload_id` reference integrations' tables, so
this revision depends on `integrations_0001`."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from tumnis.core.migration_helpers import (
    canonical_columns,
    create_tenant_table,
    drop_tenant_table,
)

revision = "calendar_0001"
down_revision = None
branch_labels = ("calendar",)
depends_on = "integrations_0001"
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "events",
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("start_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("end_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("all_day", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("attendees", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("busy", sa.Boolean, nullable=False, server_default=sa.text("true")),
        *canonical_columns("events", tainted=False),
    )


def downgrade() -> None:
    drop_tenant_table("events")
