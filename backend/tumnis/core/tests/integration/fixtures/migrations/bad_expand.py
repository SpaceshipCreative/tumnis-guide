"""Squawk fixture (T-P0-06-12): an expand revision that drops a column. Loaded only through
a temporary version_locations entry, never on the real path."""

import sqlalchemy as sa
from alembic import op

revision = "bad_expand"
down_revision = "auth_0001"
branch_labels = None
depends_on = None
phase = "expand"


def upgrade() -> None:
    op.drop_column("workspaces", "timezone")


def downgrade() -> None:
    op.add_column("workspaces", sa.Column("timezone", sa.Text, nullable=True))
