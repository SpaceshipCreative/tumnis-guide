"""workspace_keys, workspace_settings and module_flags (P0-08, SEC-6, Hosted readiness).

- workspace_keys: each workspace's data keys, wrapped by the master key version recorded
  beside them; `active` marks the one new seals use.
- workspace_settings: per-workspace settings, sealed with a data key (`key_version`).
- module_flags: a workspace's on/off switch per module (no row: on).

`app.provider_setting_keys(keys)` lets the preview boot check (tumnis.settings) see which
provider credential keys any workspace holds, as the owner-free app role: key names only,
never values.
"""

import sqlalchemy as sa
from alembic import op

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "core_0005_settings"
down_revision = "core_0006_audit"
branch_labels = None
depends_on = "auth_0001"
phase = "expand"


def upgrade() -> None:
    create_tenant_table(
        "workspace_keys",
        sa.Column("key_version", sa.Integer, nullable=False),
        sa.Column("master_key_version", sa.Integer, nullable=False),
        sa.Column("wrapped_key", sa.LargeBinary, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Index("uq_workspace_keys_ws_version", "workspace_id", "key_version", unique=True),
    )
    create_tenant_table(
        "workspace_settings",
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("value_enc", sa.LargeBinary, nullable=False),
        sa.Column("key_version", sa.Integer, nullable=False),
        sa.Index("uq_workspace_settings_ws_key", "workspace_id", "key", unique=True),
    )
    create_tenant_table(
        "module_flags",
        sa.Column("module", sa.Text, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Index("uq_module_flags_ws_module", "workspace_id", "module", unique=True),
    )
    op.execute(
        """
        CREATE FUNCTION app.provider_setting_keys(p_keys text[])
          RETURNS SETOF text
          LANGUAGE sql STABLE SECURITY DEFINER
          SET search_path = pg_catalog, public
          AS $$
            SELECT DISTINCT key FROM public.workspace_settings
            WHERE key = ANY(p_keys) AND deleted_at IS NULL
          $$
        """
    )
    op.execute("GRANT EXECUTE ON FUNCTION app.provider_setting_keys(text[]) TO tumnis_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION app.provider_setting_keys(text[])")
    drop_tenant_table("module_flags")
    drop_tenant_table("workspace_settings")
    drop_tenant_table("workspace_keys")
