"""The scheduled calendar tick (P1-09, FR-1.3): one sync per connected Google account of
every workspace, none for an account waiting for a new consent; a replayed tick starts
none twice."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tumnis.modules.calendar.tests.integration._calendar import (
    T0,
    connect,
    outbox,
    wait_for_workflows,
)

if TYPE_CHECKING:
    from dbos import DBOS, DBOSClient

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.modules.calendar.adapters.fake import FakeGoogleCalendar

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-1.3")
@pytest.mark.wp("P1-09")
async def test_tick_syncs_each_connected_account_once(  # noqa: PLR0917
    app_db: DbUrls,
    workspace: WorkspaceHandle,
    google: FakeGoogleCalendar,
    oauth_client: None,
    dbos: type[DBOS],
    dbos_client: DBOSClient,
) -> None:
    from dbos import SetWorkflowID  # noqa: PLC0415

    from tumnis.modules.calendar import api  # noqa: PLC0415
    from tumnis.modules.calendar.workflows import schedules, sync_tick  # noqa: PLC0415

    (tick,) = schedules()
    assert tick["schedule"] == "*/10 * * * *"
    assert tick["queue_name"] == "sync"

    conn_a = await connect(workspace.ctx, "a")
    conn_b = await connect(workspace.ctx, "b")
    await api.mark_needs_reauth(workspace.ctx, conn_b)

    for _ in range(2):  # the same tick twice: DBOS replays it by its workflow id
        with SetWorkflowID(f"tick-{T0.isoformat()}"):
            await sync_tick(T0, None)

    runs = await wait_for_workflows(dbos_client, "calendar_connector_sync")
    assert [run.status for run in runs] == ["SUCCESS"]
    synced = outbox(app_db, "calendar.synced")
    assert [row["payload"]["connection_id"] for row in synced] == [str(conn_a)]
