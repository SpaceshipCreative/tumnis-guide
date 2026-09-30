"""task_tokens.project_id may be NULL: a master-profile run's workspace-scoped token (P2-02,
Scott decision 30).

Plan and notify runs on the master profile name no project, so their token names none:
it reaches no project's rows and holds only a subset of the master key's scopes. Every
project run's token still names its project (`tokens.issue_task_token` enforces which run
kinds may omit it).

`app.auth_resolve_token` answers such a token's `project_ids` as an empty array (no
project), never `{NULL}`: every release reads the array as-is and caches it in the shared
prefix cache, and an empty limit is one they all read as "no project". The function keeps
its name, signature, owner and grants (`CREATE OR REPLACE`).

Downgrade deletes the project-less tokens, restores the resolver, then NOT NULL; the
tokens are short-lived (a run's lifetime) and their runs end without them.
"""

from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "auth_0006"
down_revision = "auth_0005"
branch_labels = None
depends_on = None
phase = "expand"

_RESOLVER = """
CREATE OR REPLACE FUNCTION app.auth_resolve_token(p_kind text, p_prefix text)
RETURNS TABLE (token_id uuid, workspace_id uuid, token_hmac bytea, pepper_version int,
               scopes text[], project_ids uuid[], expires_at timestamptz,
               revoked_at timestamptz, subject_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT t.id, t.workspace_id, t.token_hmac, t.pepper_version, t.scopes,
         {project_ids}, t.expires_at, t.revoked_at, t.run_id
  FROM public.task_tokens t
  WHERE p_kind = 'task' AND t.prefix = p_prefix AND t.deleted_at IS NULL
  UNION ALL
  SELECT d.id, d.workspace_id, d.token_hmac, d.pepper_version, '{{}}'::text[], NULL::uuid[],
         NULL::timestamptz, d.revoked_at, d.runner_id
  FROM public.device_tokens d
  WHERE p_kind = 'device' AND d.prefix = p_prefix AND d.deleted_at IS NULL
$$
"""
_PROJECT_IDS = "CASE WHEN t.project_id IS NULL THEN '{}'::uuid[] ELSE ARRAY[t.project_id] END"
_PROJECT_IDS_0004 = "ARRAY[t.project_id]"


def upgrade() -> None:
    # squawk's ban-drop-not-null guards readers that assume a value. The column's readers
    # are auth's own: the resolver below (an empty project list for NULL) and `keys._row`.
    # The previous release never issues a project-less token, and reads the resolver's
    # empty list as a limit no project is in, so running both releases side by side is
    # safe (the drop is a catalog change: no table rewrite).
    op.execute(
        "-- squawk-ignore ban-drop-not-null\n"
        "ALTER TABLE task_tokens ALTER COLUMN project_id DROP NOT NULL"
    )
    op.execute(_RESOLVER.format(project_ids=_PROJECT_IDS))


def downgrade() -> None:
    op.execute("DELETE FROM task_tokens WHERE project_id IS NULL")
    op.execute(_RESOLVER.format(project_ids=_PROJECT_IDS_0004))
    op.alter_column("task_tokens", "project_id", existing_type=UUID(as_uuid=True), nullable=False)
