"""Delegations (P2-06, FR-5.2, SAF-5).

- `delegations`: one row per task the master handed to a project agent. Its `id` is also
  the child run's id and that run's `dispatch_run` workflow ID. `child_task_id` and
  `project_id` (tasks and projects rows, owned by other modules, so no cross-module
  foreign key); `parent_run_id` the master's run when it called from one; `depth` 1 or 2
  (SAF-5); `note` the master's note; `delegated_at` from the call's clock (the loop
  window is judged on it); `accepted_at` when the human accepted the child run's result
  (it resets the loop count); `tainted`: written by a key with no run (R-31, P2-08).
- `ix_delegations_ws_task`: a task's delegations in time order (the loop rule's history).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "agents_0009"
down_revision = "agents_0008"
branch_labels = None
depends_on = None
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "delegations",
        sa.Column("child_task_id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("parent_run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("depth", sa.Integer, nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("delegated_at", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("accepted_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("tainted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.CheckConstraint("depth BETWEEN 1 AND 2", name="ck_delegations_depth"),
        sa.CheckConstraint("note IS NULL OR char_length(note) <= 4000", name="ck_delegations_note"),
    )
    op.create_index(
        "ix_delegations_ws_task", "delegations", ["workspace_id", "child_task_id", "delegated_at"]
    )


def downgrade() -> None:
    drop_tenant_table("delegations")
