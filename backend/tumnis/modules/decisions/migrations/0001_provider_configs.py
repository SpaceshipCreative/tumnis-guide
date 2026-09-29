"""provider_configs (P1-01, FR-11.1): one row per AI slot per workspace, naming the primary
provider, the fallback, the pinned model version and the credential sealed with the
workspace data key (`settings_store.seal_for_workspace`).

A tenant table (row-level security, base columns); `workspace_id` leads the unique key.
The fallback, thresholds and decision log arrive in P1-02.
"""

import sqlalchemy as sa

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "decisions_0001"
down_revision = None
branch_labels = ("decisions",)
depends_on = "auth_0001"
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "provider_configs",
        sa.Column("slot", sa.Text, nullable=False),
        sa.Column("primary", sa.Text, nullable=False),
        sa.Column("fallback", sa.Text, nullable=True),
        sa.Column("credentials_enc", sa.LargeBinary, nullable=True),
        sa.Column("model_version", sa.Text, nullable=False),
        sa.CheckConstraint(
            "slot IN ('decisions', 'generation', 'speech', 'embeddings')",
            name="ck_provider_configs_slot",
        ),
        sa.Index("ux_provider_configs_ws_slot", "workspace_id", "slot", unique=True),
    )


def downgrade() -> None:
    drop_tenant_table("provider_configs")
