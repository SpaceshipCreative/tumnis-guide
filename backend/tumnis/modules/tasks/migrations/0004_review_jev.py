"""review_items: Jev's blocking-impact factor and its decision (P1-13, FR-6.1, FR-11.4).

- `jev_factor`: the multiplier Jev's blocking-impact Score gives the item's deterministic
  impact when the decision applied (0.5 to 1.5, `rules.jev_factor`); NULL until the
  decision is made, and read as 1.0 then (with Decisions down the count alone orders).
- `decision_id`: the `decision_log` row that set it (no foreign key: decisions owns that
  table, and T-P0-18-17 lets the tasks tables reference only their own neighbours).

Both are nullable additions (expand). `blocking_impact` stays text (P0-18): the queue casts
it to float8 in its order.

Chained after P0-24's undo log (tasks_0003).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION, UUID

revision = "tasks_0004"
down_revision = "tasks_0003"
branch_labels = None
depends_on = None
phase = "expand"


def upgrade() -> None:
    op.add_column("review_items", sa.Column("jev_factor", DOUBLE_PRECISION, nullable=True))
    op.add_column("review_items", sa.Column("decision_id", UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    op.drop_column("review_items", "decision_id")
    op.drop_column("review_items", "jev_factor")
