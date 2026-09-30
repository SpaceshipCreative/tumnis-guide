"""Project profile provisioning (P1-06, FR-2.1, FR-5.10).

- `agent_profiles.provision_mode`: how the project's profile came to be: `create` (a new
  profile installed from the project template) or `link` (an existing profile on the agent
  server). A retry provisions the same way.
- `agent_profiles.provision_attempts`: the retries after the first provision (0 before
  any); attempt n runs as workflow `provision:<project id>:<n>`.

The check is added NOT VALID: every new or changed row is checked, and no scan of
`agent_profiles` runs inside the migration's transaction (squawk's
constraint-missing-not-valid). Existing rows take the default, `create`, which it allows.
"""

import sqlalchemy as sa
from alembic import op

revision = "agents_0002"
down_revision = "agents_0001"
branch_labels = None
depends_on = None
phase = "expand"


def upgrade() -> None:
    op.add_column(
        "agent_profiles",
        sa.Column("provision_mode", sa.Text, nullable=False, server_default="create"),
    )
    op.add_column(
        "agent_profiles",
        sa.Column("provision_attempts", sa.Integer, nullable=False, server_default="0"),
    )
    op.execute(
        "ALTER TABLE agent_profiles ADD CONSTRAINT ck_agent_profiles_provision_mode"
        " CHECK (provision_mode IN ('create', 'link')) NOT VALID"
    )


def downgrade() -> None:
    op.drop_constraint("ck_agent_profiles_provision_mode", "agent_profiles", type_="check")
    op.drop_column("agent_profiles", "provision_attempts")
    op.drop_column("agent_profiles", "provision_mode")
