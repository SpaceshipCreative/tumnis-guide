"""sync_state keeps one cursor per (connection, scope) (P3-02), contract: the old unique
index on (workspace, connection) goes, now that `integrations_0003` added the one with the
scope and the code writes through it. Nothing the running release needs is lost: every
row it wrote has scope `default`."""

import sqlalchemy as sa
from alembic import op

revision = "integrations_0004"
down_revision = "integrations_0003"
branch_labels = None
depends_on = None
phase = "contract"


def upgrade() -> None:
    op.drop_index("uq_sync_state_ws_connection", table_name="sync_state")


def downgrade() -> None:
    # A downgrade with more than one scope per connection would fail here; keep the
    # default scope's row of each connection.
    op.execute(sa.text("DELETE FROM sync_state WHERE scope <> 'default'"))
    op.create_index(
        "uq_sync_state_ws_connection", "sync_state", ["workspace_id", "connection_id"], unique=True
    )
