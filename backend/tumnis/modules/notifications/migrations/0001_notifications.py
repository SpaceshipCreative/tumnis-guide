"""The notifications tables (P4-05 browser push, the seam P2-16's Discord delivery extends;
FR-8.1, FR-8.3, FR-8.4).

- `notifications`: one row per thing that reaches the human (a review item, a focus event,
  or the one push a flushed batch sends), with the level in force, the decision P2-16's
  rules made (`now` or `batch`) and, for a batched row, when the next natural break
  released it. `payload` is the minimal push text (Data flow rule 6); `dedupe_key` (the
  event id) makes a re-run subscriber write once.
- `push_subscriptions`: a browser's Web Push subscription (endpoint and its two keys), its
  last success and consecutive failures. A gone subscription (404/410) is soft-deleted.
- `delivery_attempts`: every attempt to deliver a notification on a channel (`push` here;
  P2-16 adds `discord`), with its outcome and the push service's status code.

No foreign key from attempts to subscriptions: a gone subscription keeps its attempts.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "notifications_0001"
down_revision = None
branch_labels = ("notifications",)
depends_on = ("auth_0001",)
phase = "expand"

LEVELS = "'quiet', 'nudge', 'coach', 'guardrail'"
DECISIONS = "'now', 'batch'"
CHANNELS = "'push'"
STATUSES = "'sent', 'gone', 'failed', 'rejected'"
TZ = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    create_tenant_table(
        "notifications",
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("target_type", sa.Text, nullable=True),
        sa.Column("target_id", UUID(as_uuid=True), nullable=True),
        sa.Column("project_id", UUID(as_uuid=True), nullable=True),
        sa.Column("level", sa.Text, nullable=False),
        sa.Column("decision", sa.Text, nullable=False),
        sa.Column("released_at", TZ, nullable=True),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("dedupe_key", sa.Text, nullable=False),
        sa.CheckConstraint(f"level IN ({LEVELS})", name="ck_notifications_level"),
        sa.CheckConstraint(f"decision IN ({DECISIONS})", name="ck_notifications_decision"),
        sa.Index("uq_notifications_ws_dedupe", "workspace_id", "dedupe_key", unique=True),
        sa.Index(
            "ix_notifications_ws_held",
            "workspace_id",
            "created_at",
            postgresql_where=sa.text("decision = 'batch' AND released_at IS NULL"),
        ),
    )
    create_tenant_table(
        "push_subscriptions",
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("endpoint", sa.Text, nullable=False),
        sa.Column("p256dh", sa.Text, nullable=False),
        sa.Column("auth", sa.Text, nullable=False),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("last_success_at", TZ, nullable=True),
        sa.Column("failures", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.CheckConstraint("failures >= 0", name="ck_push_subscriptions_failures"),
        sa.Index(
            "uq_push_subscriptions_ws_endpoint",
            "workspace_id",
            "endpoint",
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
    )
    create_tenant_table(
        "delivery_attempts",
        sa.Column(
            "notification_id",
            UUID(as_uuid=True),
            sa.ForeignKey("notifications.id"),
            nullable=False,
        ),
        sa.Column("channel", sa.Text, nullable=False),
        sa.Column("subscription_id", UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("status_code", sa.Integer, nullable=True),
        sa.Column("attempted_at", TZ, nullable=False),
        sa.CheckConstraint(f"channel IN ({CHANNELS})", name="ck_delivery_attempts_channel"),
        sa.CheckConstraint(f"status IN ({STATUSES})", name="ck_delivery_attempts_status"),
        sa.Index("ix_delivery_attempts_ws_notification", "workspace_id", "notification_id"),
    )


def downgrade() -> None:
    drop_tenant_table("delivery_attempts")
    drop_tenant_table("push_subscriptions")
    drop_tenant_table("notifications")
