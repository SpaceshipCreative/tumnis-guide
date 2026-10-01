"""The focus tables (P2-15, FR-10.1 to FR-10.9).

- `focus_sessions`: one In progress task's check-in session: the level and cadence it
  started with, its back-off state (`streak`, `doubled`, `last_check_at`,
  `snoozed_until`), the latest git or agent activity seen on the task, and the
  `focus_session` workflow that keeps its time. One open session per task.
- `focus_events`: every focus event fired, with the level and rule that produced it
  (FR-10.9) and the message shown; `dedupe_key` makes a re-run firing step write once.
- `focus_responses`: the one-tap answers (FR-10.4) and "less of this" (FR-10.9).
- `focus_overrides`: today's level, one per local day (FR-10.1).

No foreign key to `tasks` or `daily_plans` (as `plan_items`): another module's rows.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "focus_0001"
down_revision = None
branch_labels = ("focus",)
depends_on = ("auth_0001",)
phase = "expand"

LEVELS = "'quiet', 'nudge', 'coach', 'guardrail'"
KINDS = "'block_start', 'not_started', 'check_in_due', 'switched', 'stuck', 'block_end', 'day_end'"
RESPONSES = "'still_on_it', 'switched', 'stuck', 'snooze', 'less_of_this'"
TZ = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    create_tenant_table(
        "focus_sessions",
        sa.Column("task_id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=True),
        sa.Column("level", sa.Text, nullable=False),
        sa.Column("cadence_min", sa.Integer, nullable=False),
        sa.Column("started_at", TZ, nullable=False),
        sa.Column("ended_at", TZ, nullable=True),
        sa.Column("streak", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("doubled", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("last_check_at", TZ, nullable=False),
        sa.Column("snoozed_until", TZ, nullable=True),
        sa.Column("last_activity_at", TZ, nullable=True),
        sa.Column("workflow_id", sa.Text, nullable=False),
        sa.CheckConstraint(f"level IN ({LEVELS})", name="ck_focus_sessions_level"),
        sa.CheckConstraint("cadence_min > 0", name="ck_focus_sessions_cadence"),
        sa.Index(
            "uq_focus_sessions_ws_task_open",
            "workspace_id",
            "task_id",
            unique=True,
            postgresql_where=sa.text("ended_at IS NULL"),
        ),
    )
    create_tenant_table(
        "focus_events",
        sa.Column(
            "session_id", UUID(as_uuid=True), sa.ForeignKey("focus_sessions.id"), nullable=True
        ),
        sa.Column("task_id", UUID(as_uuid=True), nullable=True),
        sa.Column("plan_id", UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("fired_at", TZ, nullable=False),
        sa.Column("level", sa.Text, nullable=False),
        sa.Column("rule", sa.Text, nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("dedupe_key", sa.Text, nullable=False),
        sa.CheckConstraint(f"kind IN ({KINDS})", name="ck_focus_events_kind"),
        sa.CheckConstraint(f"level IN ({LEVELS})", name="ck_focus_events_level"),
        sa.Index("uq_focus_events_ws_dedupe", "workspace_id", "dedupe_key", unique=True),
        sa.Index("ix_focus_events_ws_fired", "workspace_id", "fired_at"),
    )
    create_tenant_table(
        "focus_responses",
        sa.Column("event_id", UUID(as_uuid=True), sa.ForeignKey("focus_events.id"), nullable=False),
        sa.Column("task_id", UUID(as_uuid=True), nullable=True),
        sa.Column("response", sa.Text, nullable=False),
        sa.Column("responded_at", TZ, nullable=False),
        sa.Column("to_task_id", UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(f"response IN ({RESPONSES})", name="ck_focus_responses_response"),
        sa.Index("ix_focus_responses_ws_event", "workspace_id", "event_id"),
    )
    create_tenant_table(
        "focus_overrides",
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("level", sa.Text, nullable=False),
        sa.CheckConstraint(f"level IN ({LEVELS})", name="ck_focus_overrides_level"),
        sa.Index("uq_focus_overrides_ws_day", "workspace_id", "day", unique=True),
    )


def downgrade() -> None:
    drop_tenant_table("focus_overrides")
    drop_tenant_table("focus_responses")
    drop_tenant_table("focus_events")
    drop_tenant_table("focus_sessions")
