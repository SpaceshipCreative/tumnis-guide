"""projects, project_links and project_policies (P0-17, FR-2.1, FR-5.6).

- projects: one row per project. `sort_key` is a fractional ranking key (core/rank.py)
  compared bytewise (`COLLATE "C"`: under a locale collation `a0V` and `a0l` can sort
  differently from Python). A project keeps its code at a path on the agent server or at a
  repository URL, never both (`ck_projects_one_code_location`). Names are unique per
  workspace, ignoring case, among live rows.
- project_links: people, domains, repositories and Coolify apps linked to a project.
- project_policies: the approval policy (FR-5.6) and runaway limits (SAF-5), one per
  project.

All three are tenant tables; `workspace_id` leads every multi-column index.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "projects_0001"
down_revision = None
branch_labels = ("projects",)
depends_on = "auth_0001"
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "projects",
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("client", sa.Text, nullable=True),
        sa.Column("goal", sa.Text, nullable=True),
        sa.Column("deadline", sa.Date, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'active'")),
        sa.Column("sort_key", sa.Text(collation="C"), nullable=False),
        sa.Column("code_path", sa.Text, nullable=True),
        sa.Column("repo_url", sa.Text, nullable=True),
        sa.Column("profile_name", sa.Text, nullable=True),
        sa.Column("archived_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "local_decisions_only", sa.Boolean, nullable=False, server_default=sa.text("false")
        ),
        sa.Column("focus_cadence_min", sa.Integer, nullable=True),
        sa.Column("subtask_threshold_min", sa.Integer, nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'on_hold', 'completed')", name="ck_projects_status"
        ),
        sa.CheckConstraint(
            "NOT (code_path IS NOT NULL AND repo_url IS NOT NULL)",
            name="ck_projects_one_code_location",
        ),
        sa.Index(
            "ix_projects_ws_sort",
            "workspace_id",
            "sort_key",
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
        sa.Index(
            "ux_projects_ws_name",
            "workspace_id",
            sa.text("lower(name)"),
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
    )
    create_tenant_table(
        "project_links",
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.CheckConstraint(
            "kind IN ('person', 'domain', 'repo', 'coolify_app')", name="ck_project_links_kind"
        ),
        sa.Index("ix_project_links_ws_project", "workspace_id", "project_id"),
    )
    create_tenant_table(
        "project_policies",
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("gated", ARRAY(sa.Text), nullable=False),
        sa.Column("allowed", ARRAY(sa.Text), nullable=False),
        sa.Column("tool_allowlist", ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("max_concurrent_runs", sa.Integer, nullable=False),
        sa.Column("max_run_minutes", sa.Integer, nullable=False),
        sa.Column("max_tasks_per_run", sa.Integer, nullable=False),
        sa.Index("ux_project_policies_ws_project", "workspace_id", "project_id", unique=True),
    )


def downgrade() -> None:
    drop_tenant_table("project_policies")
    drop_tenant_table("project_links")
    drop_tenant_table("projects")
