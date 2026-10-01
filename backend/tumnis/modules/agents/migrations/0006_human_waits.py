"""Questions and approvals: an agent's waits on the human (P2-05, FR-5.6, FR-5.7, SEC-3).

Every wait is a row (the truth) and a workflow (the reaction): a re-sent `ask_human` or
`request_approval` reads the row first, so an answer is never lost while the workflow is
being replaced (a deploy to a new application version).

- questions: the run's question (`prompt`, optional one-tap `choices`), `pending` until the
  human answers (`answered`, with `answer`); `review_item_id` is the `question` review
  item, `workflow_id` the `question_flow` that waits on it (`question:<id>:<app version>`).
- approvals: the run's request to take an action (`action_class`, `description`,
  `target`), with the policy `rule` that judged it; `pending` until the human approves
  or denies it with a reason (SEC-3), or `approved` at once when the policy or Decisions
  allows it. `workflow_id` is the `approval_flow` (`approval:<id>:<app version>`).

`decided_by` is the actor who decided (`user:<id>`, or `system` for an automatic
approval). Both tables are new, so nothing is backfilled.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "agents_0006"
down_revision = "agents_0005"
branch_labels = None
depends_on = None
phase = "expand"

TS = sa.TIMESTAMP(timezone=True)


def _wait_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("task_id", UUID(as_uuid=True), nullable=True),
        sa.Column("review_item_id", UUID(as_uuid=True), nullable=True),
        sa.Column("workflow_id", sa.Text, nullable=True),
        sa.Column("decided_by", sa.Text, nullable=True),
        sa.Column("decided_at", TS, nullable=True),
    ]


def upgrade() -> None:
    create_tenant_table(
        "questions",
        *_wait_columns(),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("choices", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'pending'")),
        sa.Column("answer", sa.Text, nullable=True),
        sa.CheckConstraint("status IN ('pending', 'answered')", name="ck_questions_status"),
        sa.Index("ix_questions_ws_run", "workspace_id", "run_id"),
    )
    create_tenant_table(
        "approvals",
        *_wait_columns(),
        sa.Column("action_class", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("target", sa.Text, nullable=True),
        sa.Column("rule", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'pending'")),
        sa.Column("reason", sa.Text, nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'denied')", name="ck_approvals_status"
        ),
        sa.Index("ix_approvals_ws_run", "workspace_id", "run_id"),
    )


def downgrade() -> None:
    drop_tenant_table("approvals")
    drop_tenant_table("questions")
