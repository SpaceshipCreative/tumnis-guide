"""tasks: where the first action came from and how the enrichment went (P1-08, FR-4.4, FR-4.6).

- `first_action_source`: `placeholder` (the Generation slot's stand-in, shown until the
  project agent answers) or `agent` (the project agent's enrichment); NULL for a first
  action a person or an API client wrote, or none.
- `enrichment_status`: the project agent's enrichment of the task: `pending`, `running`,
  `done`, `agent_offline`, `not_provisioned` or `failed`; NULL before one starts.

Both nullable additions with CHECKs (expand). The checks are added NOT VALID: every new
or changed row is checked, and no scan of `tasks` runs inside the migration's transaction
(squawk's constraint-missing-not-valid); existing rows hold NULL, which they allow.
Chained after P1-07's label columns (tasks_0006).
"""

import sqlalchemy as sa
from alembic import op

revision = "tasks_0007"
down_revision = "tasks_0006"
branch_labels = None
depends_on = None
phase = "expand"

FIRST_ACTION_SOURCES = ("placeholder", "agent")
ENRICHMENT_STATUSES = ("pending", "running", "done", "agent_offline", "not_provisioned", "failed")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.add_column("tasks", sa.Column("first_action_source", sa.Text, nullable=True))
    op.add_column("tasks", sa.Column("enrichment_status", sa.Text, nullable=True))
    for name, column, values in (
        ("ck_tasks_first_action_source", "first_action_source", FIRST_ACTION_SOURCES),
        ("ck_tasks_enrichment_status", "enrichment_status", ENRICHMENT_STATUSES),
    ):
        op.execute(
            f"ALTER TABLE tasks ADD CONSTRAINT {name} CHECK ({_in(column, values)}) NOT VALID"
        )


def downgrade() -> None:
    op.drop_constraint("ck_tasks_enrichment_status", "tasks", type_="check")
    op.drop_constraint("ck_tasks_first_action_source", "tasks", type_="check")
    op.drop_column("tasks", "enrichment_status")
    op.drop_column("tasks", "first_action_source")
