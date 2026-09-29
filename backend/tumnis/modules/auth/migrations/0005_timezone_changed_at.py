"""workspaces.timezone_changed_at: when the workspace timezone last changed (P0-19, FR-3.6,
REL-6). The day close anchors on it, so only a real timezone change moves the next close;
any other settings write leaves it. NULL (never changed) reads as the row's creation."""

import sqlalchemy as sa
from alembic import op

revision = "auth_0005"
down_revision = "auth_0004"
branch_labels = None
depends_on = None
phase = "expand"


def upgrade() -> None:
    op.add_column(
        "workspaces", sa.Column("timezone_changed_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("workspaces", "timezone_changed_at")
