"""A sync that ends without its finish step never strands its connection (P3-02 follow-up;
REL-3, FR-14.5). When `integrations_sync_finish` runs out of retries the workflow ends in
ERROR with the connection still `syncing`. No sync workflow holds it any more, so the next
tick treats that `syncing` as a lapsed lease: it syncs the connection again, and the new
sync's finish sets the status the table gives."""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.integrations.tests.integration._connections import ready, run_sync, wired

if TYPE_CHECKING:
    from dbos import DBOS, DBOSClient

    from tests._pg import DbUrls
    from tests.fixtures import MasterKeyFile, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


async def _settled(dbos_client: DBOSClient, connection_id: uuid.UUID) -> list[Any]:
    """The connection's sync workflows once none is queued or running and one succeeded."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 30
    while True:
        flows = [
            f
            for f in await asyncio.to_thread(
                dbos_client.list_workflows, name="integrations_connector_sync"
            )
            if f.input is not None and f.input["args"][1] == str(connection_id)
        ]
        active = any(f.status in {"PENDING", "ENQUEUED"} for f in flows)
        if (not active and any(f.status == "SUCCESS" for f in flows)) or loop.time() > deadline:
            return flows
        await asyncio.sleep(0.1)


@pytest.mark.req("REL-3", "FR-14.5")
@pytest.mark.wp("P3-02")
async def test_a_sync_whose_finish_failed_is_recovered_by_the_next_tick(  # noqa: PLR0917
    app_db: DbUrls,
    workspace: WorkspaceHandle,
    master_key_file: MasterKeyFile,
    dbos: type[DBOS],
    dbos_client: DBOSClient,
    clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dbos import SetWorkflowID  # noqa: PLC0415
    from dbos import error as dbos_error  # noqa: PLC0415

    from tumnis.modules.integrations import api  # noqa: PLC0415
    from tumnis.modules.integrations.adapters.fake_source import FakeSource  # noqa: PLC0415
    from tumnis.modules.integrations.workflows import connector_sync_tick  # noqa: PLC0415

    connection_id = await ready(workspace.ctx)

    async def finish_fails(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("the database went away")

    with wired(clock, sources={"fake": FakeSource(clock=clock)}):
        monkeypatch.setattr(api, "finish_sync", finish_fails)
        with pytest.raises(dbos_error.DBOSMaxStepRetriesExceeded):
            await run_sync(workspace.id, connection_id)
        stuck = await api.get_connection(workspace.ctx, connection_id)
        assert stuck.status == "syncing"
        monkeypatch.undo()

        minute = clock.now().replace(second=0, microsecond=0)
        with SetWorkflowID(f"test-tick-{uuid.uuid4()}"):
            await connector_sync_tick(minute, None)
        flows = await _settled(dbos_client, connection_id)

    recovered = await api.get_connection(workspace.ctx, connection_id)
    assert recovered.status == "ok"
    assert recovered.last_success_at == clock.now()
    assert sorted(f.status for f in flows) == ["ERROR", "SUCCESS"]
