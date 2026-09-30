"""task_changes: the undo log (P0-24, R-09, UX 9).

Every task write records the undoable fields it changed (`rules.UNDO_FIELDS`, never the
history fields): `before` and `after` hold only those, and `change_id` is the id the write
answers. `task_version` is the task's version that the write left: an undo must name it,
so an older change can't overwrite a newer write. `undone_at` marks a change put back by
`POST /v1/tasks/{id}/undo`; an undo is a change of its own. `workspace_id` leads both
indexes. A task's changes go with it when the trash purge hard-deletes it (ON DELETE
CASCADE, as P0-19 made its comments and context links).

Chained after P0-19's recurrence revision (tasks_0002).
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "tasks_0003"
down_revision = "tasks_0002"
branch_labels = None
depends_on = None
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "task_changes",
        sa.Column(
            "task_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("change_id", UUID(as_uuid=True), nullable=False),
        sa.Column("actor", sa.Text, nullable=False),
        sa.Column("before", JSONB, nullable=False),
        sa.Column("after", JSONB, nullable=False),
        sa.Column("task_version", sa.Integer, nullable=False),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.Index("ux_task_changes_ws_change", "workspace_id", "change_id", unique=True),
        sa.Index("ix_task_changes_ws_task", "workspace_id", "task_id"),
    )


def downgrade() -> None:
    drop_tenant_table("task_changes")
