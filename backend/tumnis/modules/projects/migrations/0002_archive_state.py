"""projects.archive_state (P2-18, FR-5.10): where a project's archive stands.

NULL for a live project (and for one archived before P2-18, whose data was never moved);
`archiving` while `archive_project` moves its data out, `archived` once it has, and
`unarchiving` while `unarchive_project` brings it back. `archived_at` keeps its meaning
(hidden from the lists), set and cleared by the routes at once.
"""

import sqlalchemy as sa
from alembic import op

revision = "projects_0002"
down_revision = "projects_0001"
branch_labels = None
depends_on = None
phase = "expand"


def upgrade() -> None:
    op.add_column("projects", sa.Column("archive_state", sa.Text, nullable=True))
    # A new, nullable column holds no rows to check: NOT VALID skips a pointless scan.
    op.execute(
        "ALTER TABLE projects ADD CONSTRAINT ck_projects_archive_state"
        " CHECK (archive_state IN ('archiving', 'archived', 'unarchiving')) NOT VALID"
    )


def downgrade() -> None:
    op.drop_constraint("ck_projects_archive_state", "projects", type_="check")
    op.drop_column("projects", "archive_state")
