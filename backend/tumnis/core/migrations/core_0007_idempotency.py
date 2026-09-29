"""idempotency_keys: stored responses for idempotent writes (P0-10, REL-2).

Workspace-scoped with its own columns (`create_tenant_table(..., base=False)`: `id`,
`workspace_id`, the (workspace_id, id) index and the tenant policy; no version or
deleted_at, so the table registry allow-lists it). One row per (workspace, principal, key);
the unique index is what a concurrent retry waits on. `ix_idempotency_expires` serves the
housekeeping purge (P0-19).
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "core_0007_idempotency"
down_revision = "core_p027_metrics"
branch_labels = None
depends_on = "auth_0001"  # idempotency_keys.workspace_id -> workspaces
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "idempotency_keys",
        sa.Column("principal", sa.Text, nullable=False),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("route", sa.Text, nullable=False),
        sa.Column("method", sa.Text, nullable=False),
        sa.Column("request_hash", sa.LargeBinary, nullable=False),
        sa.Column("response_status", sa.Integer, nullable=True),
        sa.Column("response_headers", JSONB, nullable=True),
        sa.Column("response_body", sa.LargeBinary, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Index(
            "uq_idempotency_ws_principal_key", "workspace_id", "principal", "key", unique=True
        ),
        sa.Index("ix_idempotency_expires", "expires_at"),
        base=False,
    )


def downgrade() -> None:
    drop_tenant_table("idempotency_keys")
