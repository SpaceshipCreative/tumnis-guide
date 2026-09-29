"""Identity: users, memberships, sessions, auth_throttle and the pre-auth definer functions
(P0-13, SEC-1, Hosted readiness).

- `users` is a global identity table (one person across workspaces): no workspace_id, a
  `self_only` policy keyed on `app.user_id`, so the app role reads and writes only the
  signed-in user's row. `totp_confirmed_at` marks setup as complete: until the first code
  is confirmed no session can exist.
- `memberships` and `sessions` are tenant tables (create_tenant_table).
- `auth_throttle` is global (counters exist before any workspace is known) and has no app
  role grant: it is reached only through `app.auth_throttle_lock` and `_put`.
- Pre-auth lookups cross the tenant fence, so they are SECURITY DEFINER functions on the
  closed allow-list (tests/meta/test_security_definer.py).

Each CREATE TABLE starts its line: the table registry reads tables from the offline SQL.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import BYTEA, INET, UUID

from tumnis.core.migration_helpers import create_tenant_table, drop_tenant_table

revision = "auth_0003"
down_revision = "auth_0002"
branch_labels = None
depends_on = None
phase = "expand"

APP_ROLE = "tumnis_app"
SELF_ONLY = "id = NULLIF(current_setting('app.user_id', true), '')::uuid"

USERS = f"""
CREATE TABLE users (
  id                 uuid PRIMARY KEY DEFAULT uuidv7(),
  email              citext NOT NULL UNIQUE,
  password_hash      text NOT NULL,
  totp_secret_enc    bytea,
  totp_key_version   int,
  totp_last_step     bigint NOT NULL DEFAULT 0,
  totp_confirmed_at  timestamptz,
  home_workspace_id  uuid NOT NULL REFERENCES workspaces(id),
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  version            int NOT NULL DEFAULT 1,
  deleted_at         timestamptz
);
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
CREATE POLICY self_only ON users FOR ALL TO {APP_ROLE} USING ({SELF_ONLY}) WITH CHECK ({SELF_ONLY})
"""

THROTTLE = f"""
CREATE TABLE auth_throttle (
  key_hash           bytea PRIMARY KEY,
  failures           int NOT NULL,
  window_started_at  timestamptz NOT NULL,
  locked_until       timestamptz
);
REVOKE ALL ON auth_throttle FROM {APP_ROLE}
"""

FUNCTIONS = (
    """
CREATE FUNCTION app.auth_login_lookup(p_email citext)
RETURNS TABLE (user_id uuid, password_hash text, workspace_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT u.id, u.password_hash, u.home_workspace_id FROM public.users u
  WHERE u.email = p_email AND u.deleted_at IS NULL
$$
""",
    """
CREATE FUNCTION app.auth_resolve_session(p_token_hmac bytea)
RETURNS TABLE (session_id uuid, user_id uuid, workspace_id uuid, last_seen_at timestamptz,
               expires_at timestamptz, revoked_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT s.id, s.user_id, s.workspace_id, s.last_seen_at, s.expires_at, s.revoked_at
  FROM public.sessions s WHERE s.token_hmac = p_token_hmac AND s.deleted_at IS NULL
$$
""",
    """
CREATE FUNCTION app.auth_user_exists()
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT EXISTS (SELECT 1 FROM public.users WHERE deleted_at IS NULL)
$$
""",
    """
CREATE FUNCTION app.auth_throttle_lock(p_keys bytea[], p_now timestamptz)
RETURNS TABLE (key_hash bytea, failures int, window_started_at timestamptz,
               locked_until timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
  INSERT INTO public.auth_throttle (key_hash, failures, window_started_at)
  SELECT k, 0, p_now FROM unnest(p_keys) AS k
  ON CONFLICT DO NOTHING;
  RETURN QUERY
    SELECT t.key_hash, t.failures, t.window_started_at, t.locked_until
    FROM public.auth_throttle t WHERE t.key_hash = ANY (p_keys)
    ORDER BY t.key_hash FOR UPDATE;
END $$
""",
    """
CREATE FUNCTION app.auth_throttle_put(p_key bytea, p_failures int,
                                      p_window_started_at timestamptz,
                                      p_locked_until timestamptz)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
  IF p_failures = 0 AND p_locked_until IS NULL THEN
    DELETE FROM public.auth_throttle WHERE key_hash = p_key;
  ELSE
    UPDATE public.auth_throttle
    SET failures = p_failures, window_started_at = p_window_started_at,
        locked_until = p_locked_until
    WHERE key_hash = p_key;
  END IF;
END $$
""",
)
SIGNATURES = (
    "app.auth_login_lookup(citext)",
    "app.auth_resolve_session(bytea)",
    "app.auth_user_exists()",
    "app.auth_throttle_lock(bytea[], timestamptz)",
    "app.auth_throttle_put(bytea, int, timestamptz, timestamptz)",
)


def upgrade() -> None:
    op.execute(USERS)
    create_tenant_table(
        "memberships",
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.CheckConstraint("role IN ('owner')", name="ck_memberships_role"),
        sa.Index("uq_memberships_ws_user", "workspace_id", "user_id", unique=True),
    )
    create_tenant_table(
        "sessions",
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hmac", BYTEA, nullable=False),
        sa.Column("device_label", sa.Text, nullable=True),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("source_ip", INET, nullable=True),
        sa.Column("second_factor", sa.Text, nullable=True),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Index("uq_sessions_ws_token", "workspace_id", "token_hmac", unique=True),
        sa.Index("ix_sessions_ws_user", "workspace_id", "user_id"),
    )
    # Single column, not unique: the pre-auth lookup by token (app.auth_resolve_session).
    op.create_index("ix_sessions_token_hmac", "sessions", ["token_hmac"])
    op.execute(THROTTLE)
    for statement in FUNCTIONS:
        op.execute(statement)
    for signature in SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {APP_ROLE}")


def downgrade() -> None:
    for signature in reversed(SIGNATURES):
        op.execute(f"DROP FUNCTION {signature}")
    op.drop_table("auth_throttle")
    op.drop_index("ix_sessions_token_hmac", table_name="sessions")
    drop_tenant_table("sessions")
    drop_tenant_table("memberships")
    op.drop_table("users")
