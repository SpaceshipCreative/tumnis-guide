"""thresholds and decision_log (P1-02, FR-11.3, FR-11.4, FR-11.5).

- thresholds: one row per workspace, decision point and pinned model version, holding the
  `Threshold` as JSON. `source` says whether the value is the plan's default or set by the
  user; `needs_recheck` is raised when the pinned model changes, until the value is
  confirmed (P3-08).
- decision_log: one row per `decide` call, with the model version, the input hash, the
  names of the fields sent, the effective threshold and the outcome. Typed answers only:
  never the input text (Data flow rule 6). `overridden`, `final_value` and `outcome_at`
  stay null until the human acts on the decision (`human.decided`).

Both are tenant tables; `workspace_id` leads every multi-column index. Neither references
the subject: a subject is a task, message, note, review item, focus session or run, so
`subject_id` and `project_id` carry no foreign key and the log outlives the row it is about.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "decisions_0002"
down_revision = "decisions_0001"
branch_labels = None
depends_on = None
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "thresholds",
        sa.Column("decision_point", sa.Text, nullable=False),
        sa.Column("model_version", sa.Text, nullable=False),
        sa.Column("value", JSONB, nullable=False),
        sa.Column("source", sa.Text, nullable=False, server_default="default"),
        sa.Column("needs_recheck", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.CheckConstraint("source IN ('default', 'user')", name="ck_thresholds_source"),
        sa.Index(
            "ux_thresholds_ws_point_model",
            "workspace_id",
            "decision_point",
            "model_version",
            unique=True,
        ),
    )
    create_tenant_table(
        "decision_log",
        sa.Column("decision_point", sa.Text, nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=True),
        sa.Column("subject_type", sa.Text, nullable=False),
        sa.Column("subject_id", UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("fallback", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("fallback_reason", sa.Text, nullable=True),
        sa.Column("model_version", sa.Text, nullable=True),
        sa.Column("input_hash", sa.LargeBinary, nullable=False),
        sa.Column("fields_sent", ARRAY(sa.Text), nullable=False),
        sa.Column("answer", JSONB, nullable=True),
        sa.Column("confidence", sa.REAL, nullable=True),
        sa.Column("threshold", JSONB, nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("cached", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("overridden", sa.Boolean, nullable=True),
        sa.Column("final_value", JSONB, nullable=True),
        sa.Column("outcome_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "provider IN ('jev', 'vllm', 'fake', 'none')", name="ck_decision_log_provider"
        ),
        sa.CheckConstraint(
            "fallback_reason IN ('primary_failed', 'local_only')",
            name="ck_decision_log_fallback_reason",
        ),
        sa.CheckConstraint(
            "outcome IN ('applied', 'review', 'deterministic', 'approval_required')",
            name="ck_decision_log_outcome",
        ),
    )
    op.create_index(
        "ix_decision_log_point_time",
        "decision_log",
        ["workspace_id", "decision_point", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_decision_log_subject", "decision_log", ["workspace_id", "subject_type", "subject_id"]
    )


def downgrade() -> None:
    drop_tenant_table("decision_log")
    drop_tenant_table("thresholds")
