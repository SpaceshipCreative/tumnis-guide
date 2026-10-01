"""Detour capture at Guardrail (P4-01, FR-10.6).

`focus_events` gains, for a `switched` event that captured a detour: the task created for
it (`detour_task_id`), the task to return to (`return_to_task_id`), and the person's answer
to the return question (`return_decision`, 'return' or 'stay') with when it was given
(`decided_at`). Every column is nullable, so existing rows need nothing. The check is added
NOT VALID (squawk's constraint-missing-not-valid): new and changed rows are checked, and no
scan of `focus_events` runs inside the migration's transaction; every existing row holds
NULL, which the check allows.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "focus_0002"
down_revision = "focus_0001"
branch_labels = None
depends_on = None
phase = "expand"

TZ = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.add_column("focus_events", sa.Column("detour_task_id", UUID(as_uuid=True), nullable=True))
    op.add_column("focus_events", sa.Column("return_to_task_id", UUID(as_uuid=True), nullable=True))
    op.add_column("focus_events", sa.Column("return_decision", sa.Text, nullable=True))
    op.add_column("focus_events", sa.Column("decided_at", TZ, nullable=True))
    op.execute(
        "ALTER TABLE focus_events ADD CONSTRAINT ck_focus_events_return_decision"
        " CHECK (return_decision IN ('return', 'stay')) NOT VALID"
    )


def downgrade() -> None:
    op.drop_constraint("ck_focus_events_return_decision", "focus_events", type_="check")
    op.drop_column("focus_events", "decided_at")
    op.drop_column("focus_events", "return_decision")
    op.drop_column("focus_events", "return_to_task_id")
    op.drop_column("focus_events", "detour_task_id")
