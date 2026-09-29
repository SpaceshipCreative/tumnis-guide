"""audit_log and audit_anchors: append-only, one hash chain per workspace (P0-15, SEC-3).

Both tables keep their own columns (no version, updated_at or deleted_at: a row is never
changed) and are fenced on `workspace_id`, but their policies are split by command: the
app role may SELECT and INSERT in its workspace and has no UPDATE, DELETE or TRUNCATE
grant. The `app.audit_immutable()` trigger refuses UPDATE, DELETE and TRUNCATE for every
role, the owner included, so a change needs `ALTER TABLE ... DISABLE TRIGGER` as the owner;
the hash chain and the anchors make such a change visible to `verify_chain`.

`app.list_workspace_ids()` lets the nightly verify (app role, no tenant context) walk every
workspace.
"""

from alembic import op

revision = "core_0006_audit"
down_revision = "core_p028_ops"
branch_labels = None
depends_on = "auth_0001"
phase = "expand"

APP_ROLE = "tumnis_app"
WORKSPACE = "workspace_id = app.current_workspace_id()"

# Each CREATE TABLE starts its line: the table registry (tests/meta/_catalog.py) reads the
# tables from the offline SQL.
UPGRADE = (
    """
CREATE FUNCTION app.audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
END $$
""",
    """
CREATE TABLE audit_log (
  id              uuid PRIMARY KEY DEFAULT uuidv7(),
  workspace_id    uuid NOT NULL DEFAULT app.current_workspace_id() REFERENCES workspaces(id),
  seq             bigint NOT NULL,
  occurred_at     timestamptz NOT NULL,
  actor_type      text NOT NULL
                  CHECK (actor_type IN ('user', 'api_key', 'task_token', 'device', 'system')),
  actor_id        uuid,
  action          text NOT NULL,
  target_type     text,
  target_id       uuid,
  source_ip       inet,
  user_agent      text,
  correlation_id  text,
  reason          text,
  details         jsonb NOT NULL DEFAULT '{}'::jsonb,
  prev_hash       bytea NOT NULL,
  hash            bytea NOT NULL
)
""",
    "CREATE UNIQUE INDEX uq_audit_ws_seq ON audit_log (workspace_id, seq)",
    "CREATE INDEX ix_audit_ws_occurred ON audit_log (workspace_id, occurred_at)",
    """
CREATE TABLE audit_anchors (
  id              uuid PRIMARY KEY DEFAULT uuidv7(),
  workspace_id    uuid NOT NULL DEFAULT app.current_workspace_id() REFERENCES workspaces(id),
  seq             bigint NOT NULL,
  hash            bytea NOT NULL,
  anchored_at     timestamptz NOT NULL
)
""",
    "CREATE UNIQUE INDEX uq_audit_anchors_ws_seq ON audit_anchors (workspace_id, seq)",
    *(
        statement.format(table=table, prefix=prefix)
        for table, prefix in (("audit_log", "audit"), ("audit_anchors", "audit_anchors"))
        for statement in (
            "ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"CREATE POLICY {{prefix}}_read ON {{table}} FOR SELECT TO {APP_ROLE}"
            f" USING ({WORKSPACE})",
            f"CREATE POLICY {{prefix}}_insert ON {{table}} FOR INSERT TO {APP_ROLE}"
            f" WITH CHECK ({WORKSPACE})",
            f"REVOKE UPDATE, DELETE, TRUNCATE ON {{table}} FROM {APP_ROLE}",
            "CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table}"
            " FOR EACH ROW EXECUTE FUNCTION app.audit_immutable()",
            "CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table}"
            " FOR EACH STATEMENT EXECUTE FUNCTION app.audit_immutable()",
        )
    ),
    """
CREATE FUNCTION app.list_workspace_ids() RETURNS SETOF uuid
  LANGUAGE sql STABLE SECURITY DEFINER
  SET search_path = pg_catalog, public
  AS $$ SELECT id FROM public.workspaces ORDER BY id $$
""",
    f"GRANT EXECUTE ON FUNCTION app.list_workspace_ids() TO {APP_ROLE}",
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP FUNCTION app.list_workspace_ids()")
    op.execute("DROP TABLE audit_anchors")
    op.execute("DROP TABLE audit_log")
    op.execute("DROP FUNCTION app.audit_immutable()")
