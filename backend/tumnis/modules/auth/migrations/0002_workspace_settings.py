"""workspaces.subtask_threshold_min: when a task should be split, per workspace (P0-08,
FR-3.8, R-14). A constant default adds the column without rewriting the table."""

import sqlalchemy as sa
from alembic import op

revision = "auth_0002"
down_revision = "auth_0001"
branch_labels = None
depends_on = None
phase = "expand"


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column(
            "subtask_threshold_min", sa.Integer, nullable=False, server_default=sa.text("30")
        ),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "subtask_threshold_min")
