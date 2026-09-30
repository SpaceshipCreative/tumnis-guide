"""fake_scripts (R-37): scripts for the fakes, written by `POST /v1/test/fakes/{adapter}/script`.

Global, not tenant-scoped (no workspace_id, no RLS): a test stack's scripts for its fake
adapters, read by the fakes of the api and the worker (tumnis.core.fake_scripts). Only the
test route (mounted with fake adapters) writes it; `POST /v1/test/reset` empties it. The
default privileges (02-database.sql) give the app role what it needs. P0-06's table
registry allow-lists it with that reason.
"""

from alembic import op

revision = "core_0008_fake_scripts"
down_revision = "core_0007_idempotency"
branch_labels = None
depends_on = None
phase = "expand"

# CREATE TABLE starts its line: the table registry (tests/meta/_catalog.py) reads the
# tables from the offline SQL.
UPGRADE = (
    """
CREATE TABLE fake_scripts (
  adapter   text NOT NULL,
  match_key text NOT NULL DEFAULT '',
  script    jsonb NOT NULL,
  CONSTRAINT pk_fake_scripts PRIMARY KEY (adapter, match_key)
)
""",
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE fake_scripts")
