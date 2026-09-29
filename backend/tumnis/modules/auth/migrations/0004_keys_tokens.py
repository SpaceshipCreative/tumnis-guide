"""API keys, task tokens and device tokens, and their pre-auth lookups (P0-14, SEC-2, R-27,
ADR-0010).

- `api_keys`: name, prefix (in clear, for the lookup), `secret_hmac` = HMAC-SHA256(pepper,
  secret) with its `pepper_version`, scopes, project limits (NULL = every project),
  expiry, last use and revocation. A rotation with a grace window keeps the previous
  prefix and HMAC in `previous_*` until `previous_valid_until`.
- `task_tokens`: bound to one run and one project, scopes a subset of the issuing key's;
  valid until the run ends (`expires_at` is only the 24 h backstop, R-29).
- `device_tokens`: one live token per runner; reissuing revokes the previous one.
- `app.auth_resolve_api_key(prefix)` and `app.auth_resolve_token(kind, prefix)` find the
  rows of a prefix before the workspace is known (SECURITY DEFINER, on the closed
  allow-list in tests/meta/test_security_definer.py).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "auth_0004"
down_revision = "auth_0003"
branch_labels = None
depends_on = None
phase = "expand"

APP_ROLE = "tumnis_app"
TS = sa.TIMESTAMP(timezone=True)

FUNCTIONS = (
    """
CREATE FUNCTION app.auth_resolve_api_key(p_prefix text)
RETURNS TABLE (key_id uuid, workspace_id uuid, secret_hmac bytea, pepper_version int,
               scopes text[], project_ids uuid[], expires_at timestamptz,
               revoked_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT k.id, k.workspace_id, k.secret_hmac, k.pepper_version, k.scopes, k.project_ids,
         k.expires_at, k.revoked_at
  FROM public.api_keys k WHERE k.prefix = p_prefix AND k.deleted_at IS NULL
  UNION ALL
  SELECT k.id, k.workspace_id, k.previous_secret_hmac, k.previous_pepper_version, k.scopes,
         k.project_ids, LEAST(k.expires_at, k.previous_valid_until), k.revoked_at
  FROM public.api_keys k WHERE k.previous_prefix = p_prefix AND k.deleted_at IS NULL
$$
""",
    """
CREATE FUNCTION app.auth_resolve_token(p_kind text, p_prefix text)
RETURNS TABLE (token_id uuid, workspace_id uuid, token_hmac bytea, pepper_version int,
               scopes text[], project_ids uuid[], expires_at timestamptz,
               revoked_at timestamptz, subject_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT t.id, t.workspace_id, t.token_hmac, t.pepper_version, t.scopes,
         ARRAY[t.project_id], t.expires_at, t.revoked_at, t.run_id
  FROM public.task_tokens t
  WHERE p_kind = 'task' AND t.prefix = p_prefix AND t.deleted_at IS NULL
  UNION ALL
  SELECT d.id, d.workspace_id, d.token_hmac, d.pepper_version, '{}'::text[], NULL::uuid[],
         NULL::timestamptz, d.revoked_at, d.runner_id
  FROM public.device_tokens d
  WHERE p_kind = 'device' AND d.prefix = p_prefix AND d.deleted_at IS NULL
$$
""",
)
SIGNATURES = (
    "app.auth_resolve_api_key(text)",
    "app.auth_resolve_token(text, text)",
)


def _secret_columns(hmac_name: str) -> list[sa.Column[object]]:
    return [
        sa.Column("prefix", sa.Text, nullable=False),
        sa.Column(hmac_name, BYTEA, nullable=False),
        sa.Column("pepper_version", sa.Integer, nullable=False),
    ]


def upgrade() -> None:
    create_tenant_table(
        "api_keys",
        sa.Column("name", sa.Text, nullable=False),
        *_secret_columns("secret_hmac"),
        sa.Column("scopes", ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("project_ids", ARRAY(UUID(as_uuid=True)), nullable=True),
        sa.Column("expires_at", TS, nullable=True),
        sa.Column("last_used_at", TS, nullable=True),
        sa.Column("revoked_at", TS, nullable=True),
        sa.Column("previous_prefix", sa.Text, nullable=True),
        sa.Column("previous_secret_hmac", BYTEA, nullable=True),
        sa.Column("previous_pepper_version", sa.Integer, nullable=True),
        sa.Column("previous_valid_until", TS, nullable=True),
        sa.Index("uq_api_keys_ws_prefix", "workspace_id", "prefix", unique=True),
    )
    # Single column, not unique: the pre-auth lookups by prefix (app.auth_resolve_api_key).
    op.create_index("ix_api_keys_prefix", "api_keys", ["prefix"])
    op.create_index(
        "ix_api_keys_previous_prefix",
        "api_keys",
        ["previous_prefix"],
        postgresql_where=sa.text("previous_prefix IS NOT NULL"),
    )

    create_tenant_table(
        "task_tokens",
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("api_key_id", UUID(as_uuid=True), sa.ForeignKey("api_keys.id"), nullable=False),
        sa.Column("scopes", ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")),
        *_secret_columns("token_hmac"),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("revoked_at", TS, nullable=True),
        sa.Index("uq_task_tokens_ws_prefix", "workspace_id", "prefix", unique=True),
        sa.Index("ix_task_tokens_ws_run", "workspace_id", "run_id"),
    )
    op.create_index("ix_task_tokens_prefix", "task_tokens", ["prefix"])

    create_tenant_table(
        "device_tokens",
        sa.Column("runner_id", UUID(as_uuid=True), nullable=False),
        *_secret_columns("token_hmac"),
        sa.Column("rotated_at", TS, nullable=True),
        sa.Column("revoked_at", TS, nullable=True),
        sa.Index("uq_device_tokens_ws_prefix", "workspace_id", "prefix", unique=True),
        sa.Index("ix_device_tokens_ws_runner", "workspace_id", "runner_id"),
    )
    op.create_index("ix_device_tokens_prefix", "device_tokens", ["prefix"])

    for statement in FUNCTIONS:
        op.execute(statement)
    for signature in SIGNATURES:
        # nosemgrep: tumnis-sql-fstring  # SIGNATURES and APP_ROLE are module constants
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        # nosemgrep: tumnis-sql-fstring  # SIGNATURES and APP_ROLE are module constants
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {APP_ROLE}")


def downgrade() -> None:
    for signature in reversed(SIGNATURES):
        # nosemgrep: tumnis-sql-fstring  # SIGNATURES is a module constant
        op.execute(f"DROP FUNCTION {signature}")
    op.drop_index("ix_device_tokens_prefix", table_name="device_tokens")
    drop_tenant_table("device_tokens")
    op.drop_index("ix_task_tokens_prefix", table_name="task_tokens")
    drop_tenant_table("task_tokens")
    op.drop_index("ix_api_keys_previous_prefix", table_name="api_keys")
    op.drop_index("ix_api_keys_prefix", table_name="api_keys")
    drop_tenant_table("api_keys")
