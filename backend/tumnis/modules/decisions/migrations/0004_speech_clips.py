"""speech_clips (P4-03, FR-11.7, SEC-10).

One row per focus message the server engine spoke: the WAV bytes, their MIME type and when
the clip stops being served (`CLIP_TTL_MIN`, an hour). Clips are served only through
`GET /v1/speech/clips/{id}`; expired rows are deleted as new clips are stored.
`message_id` is the focus event's id (no foreign key: the focus module's row), unique per
workspace so a redelivered subscriber replaces its clip.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "decisions_0004"
down_revision = "decisions_0003"
branch_labels = None
depends_on = None
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "speech_clips",
        sa.Column("message_id", UUID(as_uuid=True), nullable=False),
        sa.Column("mime", sa.Text, nullable=False),
        sa.Column("audio", sa.LargeBinary, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_speech_clips_ws_message", "speech_clips", ["workspace_id", "message_id"], unique=True
    )
    op.create_index("ix_speech_clips_ws_expires", "speech_clips", ["workspace_id", "expires_at"])


def downgrade() -> None:
    drop_tenant_table("speech_clips")
