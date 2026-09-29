"""Operations tables (P0-28): ops_backup_runs, ops_status, ops_drill_markers.

Global, not tenant-scoped (no workspace_id, no RLS): deployment-level operations data, like
deployment_marker. P0-06's table registry allow-lists them with that reason.

- ops_backup_runs: one row per pgBackRest backup, written by run-backup.sh (postgres role
  over the socket); the freshness check and the metrics read it as the app role.
- ops_status: the latest result per operations check ("backups", "restore_drill", later
  "audit_chain"), upserted by the app role; readiness and the ops gauges read it.
- ops_drill_markers: the drill's marker and fence rows, written by the drill as postgres.
"""

from alembic import op

revision = "core_p028_ops"
down_revision = "core_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ops_backup_runs (
          id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          repo        smallint NOT NULL CHECK (repo IN (1, 2)),
          type        text NOT NULL CHECK (type IN ('full', 'diff', 'incr')),
          started_at  timestamptz NOT NULL,
          finished_at timestamptz NOT NULL,
          ok          boolean NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_ops_backup_runs_last_ok "
        "ON ops_backup_runs (repo, type, finished_at DESC) WHERE ok"
    )
    op.execute(
        """
        CREATE TABLE ops_status (
          "check"    text PRIMARY KEY,
          ok         boolean NOT NULL,
          checked_at timestamptz NOT NULL,
          details    jsonb NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ops_drill_markers (
          id         uuid PRIMARY KEY,
          kind       text NOT NULL CHECK (kind IN ('marker', 'fence')),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute("GRANT SELECT ON ops_backup_runs, ops_drill_markers TO tumnis_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON ops_status TO tumnis_app")


def downgrade() -> None:
    op.execute("DROP TABLE ops_drill_markers")
    op.execute("DROP TABLE ops_status")
    op.execute("DROP TABLE ops_backup_runs")
