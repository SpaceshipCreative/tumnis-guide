"""OAuth grants in flight (P1-09, R-15): `oauth_pending`, one row per consent started.

`/v1/<module>/oauth/start` stores the PKCE verifier (sealed) under the hash of `state`;
the callback finds the row by that hash within ten minutes, once, and stores the code
(sealed); the worker exchanges it and consumes the row (code cleared, row soft-deleted).
No column holds a code, verifier or state in plaintext. Integrations owns it because every
OAuth connector shares it (Google Calendar now, Google Docs in P3-02).
"""

import sqlalchemy as sa

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "integrations_0002"
down_revision = "integrations_0001"
branch_labels = None
depends_on = None
phase = "expand"

TZ = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    create_tenant_table(
        "oauth_pending",
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("state_hash", sa.LargeBinary, nullable=False),
        sa.Column("verifier_enc", sa.LargeBinary, nullable=True),
        sa.Column("code_enc", sa.LargeBinary, nullable=True),
        sa.Column("key_version", sa.Integer, nullable=False),
        sa.Column("redirect_uri", sa.Text, nullable=False),
        sa.Column("expires_at", TZ, nullable=False),
        sa.Column("used_at", TZ, nullable=True),
        sa.Index("uq_oauth_pending_ws_state", "workspace_id", "state_hash", unique=True),
    )


def downgrade() -> None:
    drop_tenant_table("oauth_pending")
