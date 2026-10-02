"""tasks: the unattended queue flag (P4-04, FR-4.5).

- `unattended_queued_at`: when the user queued the task for the unattended window; NULL
  when it is not queued. The tick clears it in the transaction that requests the run, so
  a later tick cannot start the task again (REL-3).
- `unattended_queued_by`: who queued it (an actor ref, the shape `created_by` holds).

Both nullable (expand). The actor check is added NOT VALID: new and changed rows are
checked, and no scan of `tasks` runs inside the migration's transaction (squawk's
constraint-missing-not-valid); existing rows hold NULL, which it allows. Chained after
P2-04's results (tasks_0009).
"""

import sqlalchemy as sa
from alembic import op

revision = "tasks_0010"
down_revision = "tasks_0009"
branch_labels = None
depends_on = None
phase = "expand"

CHECK = "ck_tasks_unattended_queued_by"


def upgrade() -> None:
    op.add_column(
        "tasks", sa.Column("unattended_queued_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.add_column("tasks", sa.Column("unattended_queued_by", sa.Text, nullable=True))
    op.execute(
        f"ALTER TABLE tasks ADD CONSTRAINT {CHECK} CHECK (unattended_queued_by"
        " ~ '^(system|(user|api_key|task_token|device):[0-9a-f-]{36})$') NOT VALID"
    )


def downgrade() -> None:
    op.drop_constraint(CHECK, "tasks", type_="check")
    op.drop_column("tasks", "unattended_queued_by")
    op.drop_column("tasks", "unattended_queued_at")
