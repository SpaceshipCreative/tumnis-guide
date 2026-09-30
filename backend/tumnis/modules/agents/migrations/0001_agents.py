"""runners, agent_profiles, runs, run_events and runner_messages (P1-04, FR-5.1, FR-5.9,
FR-5.11, FR-14.6, R-22).

- runners: one per runner daemon; its device token lives in auth's `device_tokens`.
  `inventory` is the ProfileInfo list from its last register.
- agent_profiles: the Hermes profiles Tumnis may run: one live master per workspace, one
  live agent per project. The Hermes distribution version is `profile_version` (the base
  `version` column is the row version).
- runs: every agent run; `kind` and `status` hold the whole R-22 vocabulary from the first
  revision, so later phases change behavior and never these checks.
- run_events: what happened in a run, unique per (workspace, message), so a replayed
  message lands once.
- runner_messages: the mailbox between the worker and a runner's socket, both ways;
  `message_id` is unique, so a replayed step or frame is written once.

All are tenant tables; `workspace_id` leads every multi-column index.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "agents_0001"
down_revision = None
branch_labels = ("agents",)
depends_on = "auth_0001"
phase = "expand"

NAME_CHECK = "name ~ '^[a-z0-9][a-z0-9-]{0,62}$'"
TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    create_tenant_table(
        "runners",
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("host", sa.Text, nullable=True),
        sa.Column("os", sa.Text, nullable=True),
        sa.Column("daemon_version", sa.Text, nullable=True),
        sa.Column("hermes_version", sa.Text, nullable=True),
        sa.Column("protocol_version", sa.Integer, nullable=True),
        sa.Column("last_heartbeat_at", TS, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'never_seen'")),
        sa.Column("inventory", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.CheckConstraint(NAME_CHECK, name="ck_runners_name"),
        sa.CheckConstraint(
            "status IN ('never_seen', 'online', 'offline')", name="ck_runners_status"
        ),
        sa.Index("ux_runners_ws_name", "workspace_id", "name", unique=True),
    )
    create_tenant_table(
        "agent_profiles",
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=True),
        sa.Column("runner_id", UUID(as_uuid=True), sa.ForeignKey("runners.id"), nullable=True),
        sa.Column("transport", sa.Text, nullable=False),
        sa.Column("endpoint", sa.Text, nullable=True),
        sa.Column("credentials_enc", BYTEA, nullable=True),
        sa.Column("profile_version", sa.Text, nullable=True),
        sa.Column("capabilities", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'registered'")),
        sa.Column("health", JSONB, nullable=True),
        sa.Column("health_checked_at", TS, nullable=True),
        sa.CheckConstraint(NAME_CHECK, name="ck_agent_profiles_name"),
        sa.CheckConstraint("role IN ('master', 'project')", name="ck_agent_profiles_role"),
        sa.CheckConstraint(
            "transport IN ('daemon', 'mcp_endpoint')", name="ck_agent_profiles_transport"
        ),
        sa.CheckConstraint(
            "status IN ('registered', 'provisioning', 'ready', 'not_provisioned', 'paused')",
            name="ck_agent_profiles_status",
        ),
        sa.Index("ux_agent_profiles_ws_name", "workspace_id", "name", unique=True),
        sa.Index(
            "ux_agent_profiles_one_master",
            "workspace_id",
            unique=True,
            postgresql_where=sa.text("role = 'master' AND deleted_at IS NULL"),
        ),
        sa.Index(
            "ux_agent_profiles_one_project_agent",
            "workspace_id",
            "project_id",
            unique=True,
            postgresql_where=sa.text("role = 'project' AND deleted_at IS NULL"),
        ),
        sa.Index("ix_agent_profiles_ws_runner", "workspace_id", "runner_id"),
    )
    create_tenant_table(
        "runs",
        sa.Column("task_id", UUID(as_uuid=True), nullable=True),
        sa.Column("profile_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("workflow_id", sa.Text, nullable=True),
        sa.Column("started_at", TS, nullable=True),
        sa.Column("finished_at", TS, nullable=True),
        sa.Column("correlation_id", sa.Text, nullable=False),
        sa.Column("tainted", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("delegation_id", UUID(as_uuid=True), nullable=True),
        sa.Column("output", JSONB, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.CheckConstraint(
            "kind IN ('enrich', 'plan', 'task', 'proposal', 'stuck', 'notify')",
            name="ck_runs_kind",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'waiting_on_human', 'held', 'succeeded', "
            "'failed', 'cancelled', 'timed_out', 'runner_lost')",
            name="ck_runs_status",
        ),
        sa.Index("ix_runs_ws_profile_status", "workspace_id", "profile_id", "status"),
        sa.Index(
            "ix_runs_ws_task",
            "workspace_id",
            "task_id",
            postgresql_where=sa.text("task_id IS NOT NULL"),
        ),
    )
    create_tenant_table(
        "run_events",
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.CheckConstraint(
            "kind IN ('dispatched', 'result', 'failed', 'log', 'tool_call', 'file')",
            name="ck_run_events_kind",
        ),
        sa.Index("ux_run_events_ws_message", "workspace_id", "message_id", unique=True),
        sa.Index("ix_run_events_ws_run", "workspace_id", "run_id"),
    )
    create_tenant_table(
        "runner_messages",
        sa.Column("runner_id", UUID(as_uuid=True), sa.ForeignKey("runners.id"), nullable=False),
        sa.Column("message_id", UUID(as_uuid=True), nullable=False),
        sa.Column("direction", sa.Text, nullable=False),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'queued'")),
        sa.Column("sent_at", TS, nullable=True),
        sa.Column("acked_at", TS, nullable=True),
        sa.CheckConstraint("direction IN ('in', 'out')", name="ck_runner_messages_direction"),
        sa.CheckConstraint(
            "status IN ('queued', 'sent', 'acked')", name="ck_runner_messages_status"
        ),
        sa.Index("ux_runner_messages_ws_message", "workspace_id", "message_id", unique=True),
        sa.Index(
            "ix_runner_messages_ws_runner_pending",
            "workspace_id",
            "runner_id",
            "created_at",
            postgresql_where=sa.text("direction = 'out' AND status <> 'acked'"),
        ),
    )


def downgrade() -> None:
    for table in ("runner_messages", "run_events", "runs", "agent_profiles", "runners"):
        drop_tenant_table(table)
