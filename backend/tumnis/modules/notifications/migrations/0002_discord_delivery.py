"""Discord delivery through the master (P2-16, FR-8.1, FR-8.2, REL-3).

- `notifications.details`: the facts the master's notify packet is built from that the row
  does not hold already (a focus event's task, rule and return-to task; a review item's
  target). Nullable: a batch row and every row before this revision have none.
- `delivery_attempts.run_id` and `error`: a Discord attempt is one `notify` run of the
  master profile (no foreign key: another module's row), and why it failed.
- `delivery_attempts.channel` takes `discord` beside `push`. The wider check is added NOT
  VALID (squawk's constraint-missing-not-valid): new and changed rows are checked, and no
  scan of `delivery_attempts` runs inside the migration's transaction; every existing row
  holds `push`, which it allows. The downgrade puts the narrow check back NOT VALID too,
  so Discord attempts made meanwhile do not block it.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "notifications_0002"
down_revision = "notifications_0001"
branch_labels = None
depends_on = None
phase = "expand"

CHECK = "ck_delivery_attempts_channel"


def upgrade() -> None:
    op.add_column("notifications", sa.Column("details", JSONB, nullable=True))
    op.add_column("delivery_attempts", sa.Column("run_id", UUID(as_uuid=True), nullable=True))
    op.add_column("delivery_attempts", sa.Column("error", sa.Text, nullable=True))
    op.drop_constraint(CHECK, "delivery_attempts", type_="check")
    op.execute(
        f"ALTER TABLE delivery_attempts ADD CONSTRAINT {CHECK}"
        " CHECK (channel IN ('push', 'discord')) NOT VALID"
    )


def downgrade() -> None:
    op.drop_constraint(CHECK, "delivery_attempts", type_="check")
    op.execute(
        f"ALTER TABLE delivery_attempts ADD CONSTRAINT {CHECK}"
        " CHECK (channel IN ('push')) NOT VALID"
    )
    op.drop_column("delivery_attempts", "error")
    op.drop_column("delivery_attempts", "run_id")
    op.drop_column("notifications", "details")
