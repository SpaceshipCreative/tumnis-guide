"""tasks: the quick-add label's reason, confidence, decision and suggestion (P1-07, FR-4.1).

- `label_reason`: the one-line reason shown with the label (or with the suggestion).
- `label_confidence`: the confidence of the decision that set the label or suggestion.
- `label_decision_id`: the `decision_log` row that set it (no foreign key: decisions owns
  that table, and T-P0-18-17 lets the tasks tables reference only their own neighbours).
- `label_suggestion`: a low-confidence answer, never written to `label` (it stays NULL,
  pending) until the human accepts it (R-08).

All nullable additions (expand). Chained after P1-13's review_items Jev columns
(tasks_0005).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION, ENUM, UUID

revision = "tasks_0006"
down_revision = "tasks_0005"
branch_labels = None
depends_on = None
phase = "expand"

LABEL = ENUM("human", "ai", "hybrid", name="task_label", create_type=False)


def upgrade() -> None:
    op.add_column("tasks", sa.Column("label_reason", sa.Text, nullable=True))
    op.add_column("tasks", sa.Column("label_confidence", DOUBLE_PRECISION, nullable=True))
    op.add_column("tasks", sa.Column("label_decision_id", UUID(as_uuid=True), nullable=True))
    op.add_column("tasks", sa.Column("label_suggestion", LABEL, nullable=True))


def downgrade() -> None:
    for column in ("label_suggestion", "label_decision_id", "label_confidence", "label_reason"):
        op.drop_column("tasks", column)
