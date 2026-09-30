"""Runner protocol 2 on the server (P2-07, FR-5.11).

- `runs.profile_version`: the profile's `VERSION` the daemon reported when the run started
  (a `status` message with state `started`). P2-04 builds on this column.
- `run_events.kind` gains `artifact` (an `upload_artifact` the server accepted) and `status`
  (a run's `status` messages: started, waiting, cancelling, gap). The check is re-added NOT
  VALID: every new or changed row is checked, and no scan of `run_events` runs inside the
  migration's transaction (squawk's constraint-missing-not-valid). Every existing row
  already holds one of the older kinds, all still allowed; a later revision may VALIDATE it.
"""

import sqlalchemy as sa
from alembic import op

revision = "agents_0002"
down_revision = "agents_0001"
branch_labels = None
depends_on = None
phase = "expand"

KINDS_V1 = "'dispatched', 'result', 'failed', 'log', 'tool_call', 'file'"
KINDS_V2 = f"{KINDS_V1}, 'artifact', 'status'"


def upgrade() -> None:
    op.add_column("runs", sa.Column("profile_version", sa.Text, nullable=True))
    op.drop_constraint("ck_run_events_kind", "run_events", type_="check")
    op.execute(
        "ALTER TABLE run_events ADD CONSTRAINT ck_run_events_kind"
        f" CHECK (kind IN ({KINDS_V2})) NOT VALID"
    )


def downgrade() -> None:
    op.drop_constraint("ck_run_events_kind", "run_events", type_="check")
    op.execute(
        "ALTER TABLE run_events ADD CONSTRAINT ck_run_events_kind"
        f" CHECK (kind IN ({KINDS_V1})) NOT VALID"
    )
    op.drop_column("runs", "profile_version")
