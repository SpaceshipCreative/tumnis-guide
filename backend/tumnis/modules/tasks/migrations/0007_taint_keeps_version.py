"""tasks: a taint raise does not bump the task's version (P2-08, SAF-1).

`tasks_touch` (`app.touch_row()`, from `create_tenant_table`) bumps `version` and
`updated_at` on every UPDATE. Linking outside content to a task raises its taint, a system
change rather than an edit: bumping the version would turn a person's edit in flight into
a stale-version conflict, and T-P0-18-11 expects a subtask's version unchanged after a URL
item is linked. The trigger now skips an UPDATE that changes `tainted` and nothing else
(the rest of the row equal as jsonb); every other UPDATE, a no-op one included, still
bumps. Live clients still refresh: `raise_taint` emits `task.updated` and marks the row
changed.

Chained after P1-07's `tasks_0006`. P2-04 and P1-08 also add a `tasks_0007`; whichever
merges later re-chains.
"""

from alembic import op

from tumnis.core.migration_helpers import TOUCH_SQL

revision = "tasks_0007"
down_revision = "tasks_0006"
branch_labels = None
depends_on = None
phase = "expand"

TAINT_ONLY_SKIPS_TOUCH = (
    "CREATE TRIGGER tasks_touch BEFORE UPDATE ON tasks FOR EACH ROW"
    " WHEN (NOT (OLD.tainted IS DISTINCT FROM NEW.tainted"
    " AND to_jsonb(OLD) - 'tainted' = to_jsonb(NEW) - 'tainted'))"
    " EXECUTE FUNCTION app.touch_row()"
)


def upgrade() -> None:
    op.execute("DROP TRIGGER tasks_touch ON tasks")
    op.execute(TAINT_ONLY_SKIPS_TOUCH)


def downgrade() -> None:
    op.execute("DROP TRIGGER tasks_touch ON tasks")
    op.execute(TOUCH_SQL.format(table="tasks"))
