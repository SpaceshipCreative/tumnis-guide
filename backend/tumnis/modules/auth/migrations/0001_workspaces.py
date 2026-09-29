"""workspaces: the tenant root (P0-06, Hosted readiness).

Its row-level security keys on `id` rather than `workspace_id`: a transaction sees only
the workspace in its context. Every module's first revision that references it adds
`depends_on = "auth_0001"`.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from tumnis.core.migration_helpers import ACTOR_CHECK, enable_tenant_rls

revision = "auth_0001"
down_revision = None
branch_labels = ("auth",)
depends_on = "core_0002"
phase = "expand"


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("timezone", sa.Text, nullable=False, server_default="UTC"),
        sa.Column("deployment_mode", sa.Text, nullable=False, server_default="self-hosted"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_by", sa.Text, nullable=False, server_default=sa.text("app.current_actor()")
        ),
        sa.CheckConstraint(
            "deployment_mode IN ('self-hosted', 'hosted')", name="ck_workspaces_deployment_mode"
        ),
        sa.CheckConstraint(ACTOR_CHECK, name="ck_workspaces_created_by"),
    )
    enable_tenant_rls("workspaces", key="id")


def downgrade() -> None:
    op.drop_table("workspaces")
