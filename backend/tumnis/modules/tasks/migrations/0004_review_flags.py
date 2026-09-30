"""review_items.flags (P2-13, FR-12.1, A12).

`flags text[] NOT NULL DEFAULT '{}'`: labels a module puts on an open review item without
deciding it. `checks_red` marks a result whose pull request has failing checks (the
`tasks.flag_red_checks` subscriber sets and clears it on `artifact.updated`). Additive: a
column with a constant default, no rewrite.

Chained after P0-24's `tasks_0003`.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision = "tasks_0004"
down_revision = "tasks_0003"
branch_labels = None
depends_on = None
phase = "expand"


def upgrade() -> None:
    op.add_column(
        "review_items",
        sa.Column("flags", ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'::text[]")),
    )


def downgrade() -> None:
    op.drop_column("review_items", "flags")
