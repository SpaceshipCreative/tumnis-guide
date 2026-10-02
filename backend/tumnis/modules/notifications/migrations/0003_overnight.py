"""The overnight batch (P4-04, FR-4.5, FR-8.4, J7).

- `notifications.decision` takes `overnight` beside `now` and `batch`: a review item an
  unattended night left (a run's result, a refusal) is held for the morning review, not
  sent at the next natural break.
- `notifications.release_at`: when an overnight row is released (the first working hour
  after the window, minus 15 minutes); NULL for every other row.
- `ix_notifications_ws_overnight` finds the held rows the release tick takes.

The wider check is added NOT VALID (squawk's constraint-missing-not-valid), as
notifications_0002 did for the channel: new and changed rows are checked, and no scan of
`notifications` runs inside the migration's transaction; every existing row holds `now` or
`batch`, which it allows. The downgrade puts the narrow check back NOT VALID too.
"""

import sqlalchemy as sa
from alembic import op

revision = "notifications_0003"
down_revision = "notifications_0002"
branch_labels = None
depends_on = None
phase = "expand"

CHECK = "ck_notifications_decision"
INDEX = "ix_notifications_ws_overnight"


def upgrade() -> None:
    op.add_column(
        "notifications", sa.Column("release_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.drop_constraint(CHECK, "notifications", type_="check")
    op.execute(
        f"ALTER TABLE notifications ADD CONSTRAINT {CHECK}"
        " CHECK (decision IN ('now', 'batch', 'overnight')) NOT VALID"
    )
    op.create_index(
        INDEX,
        "notifications",
        ["workspace_id", "release_at"],
        postgresql_where=sa.text("decision = 'overnight' AND released_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(INDEX, "notifications")
    op.drop_constraint(CHECK, "notifications", type_="check")
    op.execute(
        f"ALTER TABLE notifications ADD CONSTRAINT {CHECK}"
        " CHECK (decision IN ('now', 'batch')) NOT VALID"
    )
    op.drop_column("notifications", "release_at")
