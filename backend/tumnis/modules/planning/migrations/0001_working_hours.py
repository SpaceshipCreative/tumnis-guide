"""working_hours (P1-10, FR-4.7): the workspace's working hours per weekday.

One row per weekday (0 = Monday) with local start and end wall times, read in the workspace
timezone. A weekday without a row works the default 09:00 to 18:00 (a new workspace has
none stored); Saturday and Sunday have hours only on Re-plan.
"""

import sqlalchemy as sa

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "planning_0001"
down_revision = None
branch_labels = ("planning",)
depends_on = ("auth_0001",)
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "working_hours",
        sa.Column("weekday", sa.SmallInteger, nullable=False),
        sa.Column("start_local", sa.Time, nullable=False),
        sa.Column("end_local", sa.Time, nullable=False),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_working_hours_weekday"),
        sa.CheckConstraint("end_local > start_local", name="ck_working_hours_order"),
        sa.Index("uq_working_hours_ws_weekday", "workspace_id", "weekday", unique=True),
    )


def downgrade() -> None:
    drop_tenant_table("working_hours")
