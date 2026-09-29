"""harness_probe: scratch table for the harness self tests (test templates only)."""

import sqlalchemy as sa
from alembic import op

revision = "harness_0001"
down_revision = None
branch_labels = ("harness",)
depends_on = None


def upgrade() -> None:
    op.create_table(
        "harness_probe",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("note", sa.Text, nullable=False),
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON harness_probe TO tumnis_app")


def downgrade() -> None:
    op.drop_table("harness_probe")
