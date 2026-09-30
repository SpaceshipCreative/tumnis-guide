"""Runs v2: the run state machine's bookkeeping, ordered run events and the double-run
guard (P2-04, FR-5.2, FR-5.8, R-22).

- `runs.state_seq`: bumped on every state change, so a stale transition (an old status
  message, a replayed step) is refused by compare-and-set instead of overwriting a newer
  state. 0 for existing rows.
- `runs.active_seconds_used`: the run's active time so far (waiting on a human does not
  count), checked against its budget. 0 for existing rows.
- `runs.runner_id`: the runner the run was dispatched to (a `runners` row; NULL until
  dispatch, and for existing rows).
- `runs.stop_reason`: why a finished run stopped (budget, cancel, runner lost ...), NULL
  while it runs.
- `runs.packet`: the packet the run was dispatched with, its task token redacted, so a
  rerun or a review sees exactly what the agent was given.
- `runs.rerun_of`: the run this one reruns, NULL for a first run.
- `runs.tasks_created`: how many tasks the run created, checked against its cap. 0 for
  existing rows.
- `run_events.seq`: a total order of a run's events, from the new sequence
  `run_events_seq_seq` (OWNED BY the column, so it goes with it). The column is added
  nullable with no default (a volatile default would rewrite the table), then the default
  is set, then existing rows are numbered by (created_at, id) with `row_number()` (a
  `nextval()` in an ordered subquery is not guaranteed to follow the ORDER BY) and the
  sequence is moved past them. The column stays nullable: squawk refuses `SET NOT NULL`
  (it scans the table under an ACCESS EXCLUSIVE lock), and the app never writes it, so
  every row gets it from the default. `ux_run_events_ws_run_seq` makes it unique per run.
- `ux_runs_ws_task_kind_active`: at most one active run (queued, running, waiting on a
  human, held) of a kind per task, so a double dispatch fails in the database. The
  upgrade fails if existing rows already hold two such runs; no migration can pick which
  one to keep.

The sequence needs no GRANT: migrations run as the owner (core/alembic/env.py), and its
default privileges give the app role USAGE and SELECT on every sequence it creates in
`public` (deploy/postgres/initdb/02-database.sql). No new `kind` or `status` values: they
exist from P1-04 (R-22).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION, JSONB, UUID

revision = "agents_0005"
down_revision = "agents_0004"
branch_labels = None
depends_on = None
phase = "expand"

# The active run statuses (queued, running, waiting on a human, held), as SQL.
ACTIVE_WHERE = "status IN ('queued', 'running', 'waiting_on_human', 'held') AND task_id IS NOT NULL"
RUN_COLUMNS = (
    "state_seq",
    "active_seconds_used",
    "runner_id",
    "stop_reason",
    "packet",
    "rerun_of",
    "tasks_created",
)


def upgrade() -> None:
    op.add_column(
        "runs", sa.Column("state_seq", sa.Integer, nullable=False, server_default=sa.text("0"))
    )
    op.add_column(
        "runs",
        sa.Column(
            "active_seconds_used", DOUBLE_PRECISION, nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column("runs", sa.Column("runner_id", UUID(as_uuid=True), nullable=True))
    op.add_column("runs", sa.Column("stop_reason", sa.Text, nullable=True))
    op.add_column("runs", sa.Column("packet", JSONB, nullable=True))
    op.add_column("runs", sa.Column("rerun_of", UUID(as_uuid=True), nullable=True))
    op.add_column(
        "runs",
        sa.Column("tasks_created", sa.Integer, nullable=False, server_default=sa.text("0")),
    )

    op.add_column("run_events", sa.Column("seq", sa.BigInteger, nullable=True))
    op.execute("CREATE SEQUENCE run_events_seq_seq AS bigint OWNED BY run_events.seq")
    op.execute("ALTER TABLE run_events ALTER COLUMN seq SET DEFAULT nextval('run_events_seq_seq')")
    op.execute(
        "UPDATE run_events r SET seq = o.n FROM ("
        "SELECT id, row_number() OVER (ORDER BY created_at, id) AS n FROM run_events"
        ") o WHERE r.id = o.id"
    )
    op.execute(
        "SELECT setval('run_events_seq_seq', COALESCE(max(seq), 0) + 1, false) FROM run_events"
    )
    op.create_index(
        "ux_run_events_ws_run_seq", "run_events", ["workspace_id", "run_id", "seq"], unique=True
    )

    op.create_index(
        "ux_runs_ws_task_kind_active",
        "runs",
        ["workspace_id", "task_id", "kind"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_WHERE),
    )


def downgrade() -> None:
    op.drop_index("ux_runs_ws_task_kind_active", table_name="runs")
    op.drop_index("ux_run_events_ws_run_seq", table_name="run_events")
    op.drop_column("run_events", "seq")  # drops run_events_seq_seq with it (OWNED BY)
    for column in reversed(RUN_COLUMNS):
        op.drop_column("runs", column)
