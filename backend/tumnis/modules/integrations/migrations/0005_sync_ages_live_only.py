"""app.connector_sync_ages() leaves out connections still in `pending_auth` (P3-02
follow-up), expand only.

A connection whose sign-in was never finished is not expected to sync: counting it made
its age grow from `created_at` until TumnisConnectorSyncStale fired for the provider, and
no healthy connection could clear it (the metric is the oldest age). `auth_required`
still counts: the person has to sign in again. Same name, signature, owner and grants as
`integrations_0003`'s; CREATE OR REPLACE keeps the grants, and the attributes are
restated because a replace resets the ones it omits.
"""

from alembic import op

revision = "integrations_0005"
down_revision = "integrations_0004"
branch_labels = None
depends_on = None
phase = "expand"

_FUNCTION = """
    CREATE OR REPLACE FUNCTION app.connector_sync_ages()
      RETURNS TABLE (provider text, age_seconds double precision, items bigint)
      LANGUAGE sql STABLE SECURITY DEFINER
      SET search_path = pg_catalog, public
      AS $$
        SELECT c.provider,
               max(extract(epoch FROM now() - coalesce(c.last_success_at, c.created_at)))::float8,
               coalesce(sum(s.items), 0)::bigint
          FROM public.connections AS c
          LEFT JOIN (
            SELECT connection_id, sum(items_seen) AS items FROM public.sync_state
             GROUP BY connection_id
          ) AS s ON s.connection_id = c.id
         WHERE c.deleted_at IS NULL AND {live}
         GROUP BY c.provider
      $$
"""


def upgrade() -> None:
    op.execute(_FUNCTION.format(live="c.status NOT IN ('disabled', 'pending_auth')"))


def downgrade() -> None:
    op.execute(_FUNCTION.format(live="c.status <> 'disabled'"))
