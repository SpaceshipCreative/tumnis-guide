"""The kill switch's pauses (P2-09, SAF-4).

- `agent_pauses`: one row per pause, of the whole workspace (`scope = 'workspace'`,
  `project_id` NULL) or of one project (`scope = 'project'`, its `project_id`; a projects
  row, owned by projects, so no cross-module foreign key). `resumed_at`, `resumed_by` and
  `resume_reason` are set when a person resumes it in the app. `tainted`: the pause came
  through a key with no run, such as the master's `pause_agents` (R-31, P2-08).
- `ux_agent_pauses_ws_open`: at most one open pause (not resumed, not deleted) per
  (workspace, scope, project). `NULLS NOT DISTINCT` (PostgreSQL 15+) makes the NULL
  `project_id` of two workspace pauses collide too.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "agents_0008"
down_revision = "agents_0007"
branch_labels = None
depends_on = None
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "agent_pauses",
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=True),
        sa.Column("paused_at", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("paused_by", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("resumed_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("resumed_by", sa.Text, nullable=True),
        sa.Column("resume_reason", sa.Text, nullable=True),
        sa.Column("tainted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.CheckConstraint("scope IN ('workspace', 'project')", name="ck_agent_pauses_scope"),
        sa.CheckConstraint(
            "(scope = 'workspace') = (project_id IS NULL)", name="ck_agent_pauses_project"
        ),
        sa.CheckConstraint("char_length(reason) BETWEEN 1 AND 500", name="ck_agent_pauses_reason"),
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_agent_pauses_ws_open ON agent_pauses"
        " (workspace_id, scope, project_id) NULLS NOT DISTINCT"
        " WHERE resumed_at IS NULL AND deleted_at IS NULL"
    )


def downgrade() -> None:
    drop_tenant_table("agent_pauses")
