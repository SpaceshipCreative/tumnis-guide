"""thresholds_history and decision_evals (P3-08, FR-11.5, Quality: decision quality).

- thresholds_history: one row per human edit of a threshold on Settings > Calibration,
  with the value in force before, the new value and the reason (the audit log's
  `threshold.changed` row carries the same). `created_by` is the editor.
- decision_evals: one row per point, provider and model each time `tumnis decisions eval`
  runs: the sha256 of the evaluated set's bytes, the metrics as the page shows them (JSON
  null under 100 labeled outcomes) and when it ran. The provider is kept because vLLM
  fallback answers are evaluated apart from Jev's (FR-11.3).

Both are tenant tables; `workspace_id` leads every multi-column index. `needs_recheck` on
`thresholds` came with decisions_0002.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "decisions_0003"
down_revision = "decisions_0002"
branch_labels = None
depends_on = None
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "thresholds_history",
        sa.Column("decision_point", sa.Text, nullable=False),
        sa.Column("model_version", sa.Text, nullable=False),
        sa.Column("before", JSONB, nullable=False),
        sa.Column("after", JSONB, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
    )
    op.create_index(
        "ix_thresholds_history_point_time",
        "thresholds_history",
        ["workspace_id", "decision_point", sa.text("created_at DESC")],
    )
    create_tenant_table(
        "decision_evals",
        sa.Column("decision_point", sa.Text, nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("model_version", sa.Text, nullable=False),
        sa.Column("set_sha256", sa.Text, nullable=False),
        sa.Column("metrics", JSONB, nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_decision_evals_point_time",
        "decision_evals",
        ["workspace_id", "decision_point", sa.text("run_at DESC")],
    )


def downgrade() -> None:
    drop_tenant_table("decision_evals")
    drop_tenant_table("thresholds_history")
