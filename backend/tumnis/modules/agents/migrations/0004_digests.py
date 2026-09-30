"""digest_entries and digest_cursors (P2-03, FR-13.1, FR-13.4).

- digest_entries: what the project and workspace digests carry, one row per (event, kind),
  written by the digest subscribers (a redelivered event adds nothing: the unique key and
  `ON CONFLICT DO NOTHING`). `tx` is the writing transaction's ID
  (`pg_current_xact_id()`, an xid8, stored as numeric(20) because SQLAlchemy has no xid8
  type; the order is the same) and `seq` an identity; readers return entries in (tx, seq)
  order below the oldest running transaction (`pg_snapshot_xmin`), so none is skipped
  when transactions commit out of order.
- digest_cursors: per consumer (a profile, or a key without one) and digest
  ("project:<uuid>" or "workspace"), the position it acknowledged and the furthest one it
  was handed.

Both are tenant tables; `workspace_id` leads every multi-column index.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "agents_0004"
down_revision = "agents_0003"
branch_labels = None
depends_on = None
phase = "expand"

KINDS = (
    "'label_override', 'result_rejected', 'result_accepted', 'approval_decided',"
    " 'question_answered', 'estimate_vs_actual', 'task_changed', 'task_commented',"
    " 'document_changed', 'proposal_accepted', 'context_linked', 'focus_response',"
    " 'focus_setting_changed'"
)
TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    create_tenant_table(
        "digest_entries",
        sa.Column(
            "tx",
            sa.Numeric(20),
            nullable=False,
            server_default=sa.text("pg_current_xact_id()::text::numeric"),
        ),
        sa.Column("seq", sa.BigInteger, sa.Identity(always=True), nullable=False),
        sa.Column("event_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=True),
        sa.Column("task_id", UUID(as_uuid=True), nullable=True),
        sa.Column("occurred_at", TS, nullable=False),
        sa.Column("data", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint(f"kind IN ({KINDS})", name="ck_digest_entries_kind"),
        sa.CheckConstraint(
            "scope = 'workspace' AND project_id IS NULL"
            " OR scope = 'project' AND project_id IS NOT NULL",
            name="ck_digest_entries_scope",
        ),
        sa.Index("ux_digest_entries_event_kind", "workspace_id", "event_id", "kind", unique=True),
        sa.Index("ix_digest_project", "workspace_id", "project_id", "tx", "seq"),
        sa.Index("ix_digest_workspace", "workspace_id", "scope", "tx", "seq"),
        sa.Index("ix_digest_kind", "workspace_id", "kind", "tx", "seq"),
    )
    create_tenant_table(
        "digest_cursors",
        sa.Column("consumer_id", UUID(as_uuid=True), nullable=False),
        sa.Column("scope_key", sa.Text, nullable=False),
        sa.Column("acked_tx", sa.Numeric(20), nullable=False, server_default=sa.text("0")),
        sa.Column("acked_seq", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("issued_tx", sa.Numeric(20), nullable=False, server_default=sa.text("0")),
        sa.Column("issued_seq", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.CheckConstraint(
            "scope_key = 'workspace' OR scope_key LIKE 'project:%'",
            name="ck_digest_cursors_scope_key",
        ),
        sa.Index(
            "ux_digest_cursors_consumer", "workspace_id", "consumer_id", "scope_key", unique=True
        ),
    )


def downgrade() -> None:
    drop_tenant_table("digest_cursors")
    drop_tenant_table("digest_entries")
