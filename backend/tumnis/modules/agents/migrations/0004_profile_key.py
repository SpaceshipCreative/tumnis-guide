"""The API key a profile's runs issue task tokens from (P2-02, FR-5.4, R-27).

- `agent_profiles.api_key_id`: the profile's key (an `auth.api_keys` row, owned by auth,
  so no cross-module foreign key). Each dispatch of the profile issues a task token whose
  scopes are `run_token_scopes(kind, <the key's scopes>)`. NULL until a key is linked
  (`agents.api.set_profile_key`).

The `runs.kind` column and its values already exist from P1-04 (R-22); nothing else moves.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "agents_0004"
down_revision = "agents_0003"
branch_labels = None
depends_on = None
phase = "expand"


def upgrade() -> None:
    op.add_column("agent_profiles", sa.Column("api_key_id", UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_profiles", "api_key_id")
