"""The sync age on /metrics counts only connections that are signed in (P3-02 follow-up,
CodeRabbit on #156): a connection left in `pending_auth` (a sign-in never finished) is not
expected to sync, so it must not age into a TumnisConnectorSyncStale alert. A connection in
`auth_required` still counts: the person has to act on it."""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
import pytest
from psycopg.rows import dict_row

from tests._pg import APP, OWNER
from tumnis.modules.integrations.tests.integration._connections import ready

if TYPE_CHECKING:
    import uuid

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

STALE_S = 2 * 3600  # deploy/prometheus/alerts.yml TumnisConnectorSyncStale


def _age(db: DbUrls, connection_id: uuid.UUID, hours: int) -> None:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        conn.execute(
            "UPDATE connections SET created_at = now() - make_interval(hours => %s), "
            "last_success_at = NULL WHERE id = %s",
            (hours, connection_id),
        )


def _ages(db: DbUrls) -> dict[str, float]:
    with psycopg.connect(db.libpq(APP), row_factory=dict_row) as conn:
        found = conn.execute("SELECT provider, age_seconds FROM app.connector_sync_ages()")
        return {row["provider"]: row["age_seconds"] for row in found.fetchall()}


@pytest.mark.req("REL-5", "FR-14.4")
@pytest.mark.wp("P3-02")
async def test_pending_auth_connection_not_counted_in_sync_age(
    app_db: DbUrls, workspace: WorkspaceHandle
) -> None:
    """A `pending_auth` connection created three hours ago is left out of
    `app.connector_sync_ages()`; an `auth_required` one of the same age is counted."""
    abandoned = await ready(workspace.ctx, label="Abandoned", status="pending_auth")
    _age(app_db, abandoned, 3)
    assert "fake" not in _ages(app_db)

    lapsed = await ready(workspace.ctx, label="Lapsed", status="auth_required")
    _age(app_db, lapsed, 3)
    assert _ages(app_db)["fake"] > STALE_S
