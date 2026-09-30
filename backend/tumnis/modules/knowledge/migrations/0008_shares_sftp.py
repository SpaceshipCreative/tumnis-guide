"""Shares and SFTP host keys (P3-14, FR-15.7, FR-15.12).

- storage_locations.kind gains `share` (a mounted SMB or NFS folder, a server path whose
  marker the user places); `sftp` was allowed from knowledge_0003.
- storage_locations.status gains `pending_host_key` (an SFTP location saved, its server's
  key shown and not yet confirmed) and `host_key_changed` (the server presented another
  key: never opened until the user re-pins).
- storage_locations.host_key_pinned: the confirmed host key ("<type> <base64>", as
  known_hosts has it); host_key_pending: the key the server shows now, to confirm.

Both checks are re-added NOT VALID (like agents_0002): every new or changed row is
checked, no scan runs inside the migration's transaction, and every existing row already
holds one of the older values, all still allowed.
"""

import sqlalchemy as sa
from alembic import op

revision = "knowledge_0008"
down_revision = "knowledge_0007"
branch_labels = None
depends_on = None
phase = "expand"

KINDS_V1 = "'server_path', 's3', 'sftp'"
KINDS_V2 = f"{KINDS_V1}, 'share'"
STATUSES_V1 = "'online', 'offline'"
STATUSES_V2 = f"{STATUSES_V1}, 'pending_host_key', 'host_key_changed'"


def _check(name: str, column: str, values: str) -> None:
    op.drop_constraint(name, "storage_locations", type_="check")
    op.execute(
        f"ALTER TABLE storage_locations ADD CONSTRAINT {name}"
        f" CHECK ({column} IN ({values})) NOT VALID"
    )


def upgrade() -> None:
    op.add_column("storage_locations", sa.Column("host_key_pinned", sa.Text, nullable=True))
    op.add_column("storage_locations", sa.Column("host_key_pending", sa.Text, nullable=True))
    _check("ck_storage_locations_kind", "kind", KINDS_V2)
    _check("ck_storage_locations_status", "status", STATUSES_V2)


def downgrade() -> None:
    # Rows with the new values would fail every later update under the old constraints:
    # a share becomes the server path it is, an SFTP key state becomes offline.
    op.execute("UPDATE storage_locations SET kind = 'server_path' WHERE kind = 'share'")
    op.execute(
        "UPDATE storage_locations SET status = 'offline'"
        " WHERE status IN ('pending_host_key', 'host_key_changed')"
    )
    _check("ck_storage_locations_status", "status", STATUSES_V1)
    _check("ck_storage_locations_kind", "kind", KINDS_V1)
    op.drop_column("storage_locations", "host_key_pending")
    op.drop_column("storage_locations", "host_key_pinned")
