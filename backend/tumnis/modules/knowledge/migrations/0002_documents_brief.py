"""Text bodies and roles on `documents`, and the project foreign key (P0-17, R-15).

- `body_md`: the Markdown body of a text entry (the project brief first; FR-2.3).
- `role`: what a document is for its project; `brief` marks the pinned first entry, one
  per project (`ux_documents_ws_brief`, the conflict target of the brief subscriber).
- `project_id` references `projects` now that projects exist (P0-12 left it open). The
  constraint is added NOT VALID: every new or changed row is checked, and no scan of
  `documents` runs inside the migration's transaction (squawk's
  constraint-missing-not-valid). Rows before this revision carry no project (connector
  documents) or none that exists yet; a later revision may VALIDATE it on its own.
"""

import sqlalchemy as sa
from alembic import op

revision = "knowledge_0002"
down_revision = "knowledge_0001"
branch_labels = None
depends_on = "projects_0001"
phase = "expand"


def upgrade() -> None:
    op.add_column("documents", sa.Column("role", sa.Text, nullable=True))
    op.add_column("documents", sa.Column("body_md", sa.Text, nullable=True))
    op.create_index(
        "ux_documents_ws_brief",
        "documents",
        ["workspace_id", "project_id"],
        unique=True,
        postgresql_where=sa.text("role = 'brief'"),
    )
    op.execute(
        "ALTER TABLE documents ADD CONSTRAINT fk_documents_project_id_projects"
        " FOREIGN KEY (project_id) REFERENCES projects (id) NOT VALID"
    )


def downgrade() -> None:
    op.drop_constraint("fk_documents_project_id_projects", "documents", type_="foreignkey")
    op.drop_index("ux_documents_ws_brief", table_name="documents")
    op.drop_column("documents", "body_md")
    op.drop_column("documents", "role")
