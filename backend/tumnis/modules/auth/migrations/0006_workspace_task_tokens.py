"""task_tokens.project_id may be NULL: a master-profile run's workspace-scoped token (P2-02,
Scott decision 30).

Plan and notify runs on the master profile name no project, so their token names none:
it reaches no project's rows (the resolver reads it as an empty project limit) and holds
only a subset of the master key's scopes. Every project run's token still names its
project (`tokens.issue_task_token` enforces which run kinds may omit it).

Downgrade deletes the project-less tokens before restoring NOT NULL; they are short-lived
(a run's lifetime) and their runs end without them.
"""

from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "auth_0006"
down_revision = "auth_0005"
branch_labels = None
depends_on = None
phase = "expand"


def upgrade() -> None:
    op.alter_column("task_tokens", "project_id", existing_type=UUID(as_uuid=True), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM task_tokens WHERE project_id IS NULL")
    op.alter_column("task_tokens", "project_id", existing_type=UUID(as_uuid=True), nullable=False)
