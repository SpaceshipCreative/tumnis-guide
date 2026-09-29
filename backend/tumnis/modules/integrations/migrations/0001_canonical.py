"""The canonical integration tables (P0-12, FR-14.1 to FR-14.3).

- connections: one per (workspace, provider, account); credentials sealed (P0-08).
- raw_payloads: the provider JSON behind the records, one row per (connection, record
  type, external id) holding the latest fetch; `payload` is LZ4-compressed.
- sync_state: one cursor row per connection (P3-02 drives it).
- people, threads, messages, notes, artifacts: canonical records with the common columns
  and the unique key (workspace_id, connection_id, external_id); people also upsert on
  (workspace_id, primary_email).
- context_items: what a task, project or proposal links to, unique per (owner, target).

calendar (`events`) and knowledge (`documents`) depend on this revision for
`connection_id` and `raw_payload_id`.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, JSONB, UUID

from tumnis.core.migration_helpers import (
    canonical_columns,
    create_tenant_table,
    drop_tenant_table,
)

revision = "integrations_0001"
down_revision = None
branch_labels = ("integrations",)
depends_on = "auth_0001"
phase = "expand"

KINDS = ("email", "notes", "chat", "calendar", "code", "deploy", "knowledge")
TZ = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    kinds = ", ".join(f"'{kind}'" for kind in KINDS)
    create_tenant_table(
        "connections",
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("owner", sa.Text, nullable=True),
        sa.Column("account", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'pending_auth'")),
        sa.Column("cursor", JSONB, nullable=True),
        sa.Column("credentials_enc", sa.LargeBinary, nullable=True),
        sa.Column("key_version", sa.Integer, nullable=True),
        sa.Column("last_sync_at", TZ, nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.CheckConstraint(f"kind IN ({kinds})", name="ck_connections_kind"),
        sa.Index(
            "uq_connections_ws_provider_account", "workspace_id", "provider", "account", unique=True
        ),
    )
    create_tenant_table(
        "raw_payloads",
        sa.Column(
            "connection_id", UUID(as_uuid=True), sa.ForeignKey("connections.id"), nullable=False
        ),
        sa.Column("record_type", sa.Text, nullable=False),
        sa.Column("external_id", sa.Text, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("fetched_at", TZ, nullable=False),
        sa.Index(
            "uq_raw_payloads_ws_conn_type_ext",
            "workspace_id",
            "connection_id",
            "record_type",
            "external_id",
            unique=True,
        ),
    )
    # Applies to values stored after the change; the table is new, so to all of them.
    op.execute("ALTER TABLE raw_payloads ALTER COLUMN payload SET COMPRESSION lz4")
    create_tenant_table(
        "sync_state",
        sa.Column(
            "connection_id", UUID(as_uuid=True), sa.ForeignKey("connections.id"), nullable=False
        ),
        sa.Column("cursor", JSONB, nullable=True),
        sa.Column("last_page_at", TZ, nullable=True),
        sa.Column("items_seen", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Index("uq_sync_state_ws_connection", "workspace_id", "connection_id", unique=True),
    )
    create_tenant_table(
        "people",
        sa.Column("display_name", sa.Text, nullable=True),
        sa.Column("primary_email", CITEXT, nullable=False),
        sa.Column("emails", ARRAY(CITEXT), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("domains", ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")),
        *canonical_columns("people", tainted=True),
        sa.Index("uq_people_ws_primary_email", "workspace_id", "primary_email", unique=True),
    )
    create_tenant_table(
        "threads",
        sa.Column("subject", sa.Text, nullable=True),
        sa.Column("participants", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("last_message_at", TZ, nullable=True),
        *canonical_columns("threads", tainted=True),
    )
    create_tenant_table(
        "messages",
        sa.Column("thread_id", UUID(as_uuid=True), sa.ForeignKey("threads.id"), nullable=True),
        sa.Column("sent_at", TZ, nullable=True),
        sa.Column("from_addr", sa.Text, nullable=True),
        sa.Column("to_addrs", ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("subject", sa.Text, nullable=True),
        sa.Column("body_text", sa.Text, nullable=True),
        sa.Column("body_html_sanitized", sa.Text, nullable=True),
        sa.Column("labels", ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")),
        *canonical_columns("messages", tainted=True),
    )
    create_tenant_table(
        "notes",
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("start_at", TZ, nullable=True),
        sa.Column("end_at", TZ, nullable=True),
        sa.Column("attendees", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("body_text", sa.Text, nullable=True),
        sa.Column("action_items", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("event_external_id", sa.Text, nullable=True),
        *canonical_columns("notes", tainted=True),
    )
    create_tenant_table(
        "artifacts",
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("url", sa.Text, nullable=True),
        sa.Column("state", sa.Text, nullable=True),
        sa.Column("checks", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        *canonical_columns("artifacts", tainted=False),
    )
    create_tenant_table(
        "context_items",
        sa.Column("owner_type", sa.Text, nullable=False),
        sa.Column("owner_id", UUID(as_uuid=True), nullable=False),
        sa.Column("target_type", sa.Text, nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), nullable=True),
        sa.Column("target_url", sa.Text, nullable=True),
        sa.Column("tainted", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("added_by", sa.Text, nullable=False),
        sa.CheckConstraint(
            "target_id IS NOT NULL OR target_url IS NOT NULL", name="ck_context_items_target"
        ),
        # A record target is unique on its id; a bare URL (target_id NULL) on the URL.
        sa.Index(
            "uq_context_items_ws_owner_target",
            "workspace_id",
            "owner_type",
            "owner_id",
            "target_type",
            "target_id",
            unique=True,
            postgresql_where=sa.text("target_id IS NOT NULL"),
        ),
        sa.Index(
            "uq_context_items_ws_owner_url",
            "workspace_id",
            "owner_type",
            "owner_id",
            "target_type",
            "target_url",
            unique=True,
            postgresql_where=sa.text("target_id IS NULL"),
        ),
    )


def downgrade() -> None:
    for table in (
        "context_items",
        "artifacts",
        "notes",
        "messages",
        "threads",
        "people",
        "sync_state",
        "raw_payloads",
        "connections",
    ):
        drop_tenant_table(table)
