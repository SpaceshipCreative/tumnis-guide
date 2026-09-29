"""deployment_marker: which deployment (dev, preview, prod) a database belongs to (P0-04).

A global table, not tenant-scoped: `tumnis migrate` stamps it once, and the boot checks
read it as the app role so a preview can never serve from the production database.
"""

import sqlalchemy as sa
from alembic import op

revision = "core_0001"
down_revision = None
branch_labels = ("core",)
depends_on = None
phase = "expand"


def upgrade() -> None:
    op.create_table(
        "deployment_marker",
        sa.Column("env", sa.Text, primary_key=True),
        sa.Column("master_key_fingerprint", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("env IN ('dev', 'preview', 'prod')", name="deployment_marker_env"),
    )
    op.execute("GRANT SELECT ON deployment_marker TO tumnis_app")


def downgrade() -> None:
    op.drop_table("deployment_marker")
