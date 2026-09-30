"""Runner protocol 2 edge cases found in review (P2-07, FR-5.11, REL-4): a cancel the run's
workflow never heard of survives the workflow's own finish, and a `register` sent mid-session
cannot move the runner row off the session's protocol."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]

V2_CAPABILITIES = ["run", "health", "stream", "cancel", "upload_artifact", "worktree"]


def _owner(db: DbUrls, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        return conn.execute(query.encode(), params).fetchall()


@pytest.mark.req("REL-4")
@pytest.mark.wp("P2-07")
async def test_finish_keeps_a_cancel_the_workflow_never_heard(
    dbos: type[DBOS],
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    fake_runner: FakeRunnerFactory,
) -> None:
    """The protocol-1 fallback marks the run cancelled and tells its workflow best effort.
    When that message is lost, the workflow's wait times out; finishing then keeps the run
    `cancelled` (and its reason) instead of overwriting it with `timed_out`."""
    from tumnis.modules.agents import workflows  # noqa: PLC0415
    from tumnis.modules.agents.adapters.hermes import (  # noqa: PLC0415
        OLDER_RUNNER,
        DaemonTransport,
        HermesAgent,
    )
    from tumnis.modules.agents.tests.contract.base import make_packet  # noqa: PLC0415

    runner = fake_runner(profiles=["acme-site"])  # protocol 1
    profile_id = fake_runner.register_profile("acme-site", runner=runner)
    agent = HermesAgent(profile_id, DaemonTransport(workspace.ctx, clock))
    packet = make_packet(profile_id)
    handle = await agent.dispatch(packet)  # no workflow: nobody hears the cancel
    await agent.cancel(handle)

    outcome = await workflows.finish_step(str(workspace.id), packet.model_dump(mode="json"), None)

    assert outcome["status"] == "cancelled"
    assert outcome["error"] == OLDER_RUNNER
    assert _owner(db, "SELECT status, error FROM runs WHERE id = %s", (packet.run_id,)) == [
        ("cancelled", OLDER_RUNNER)
    ]
    failed = _owner(
        db,
        "SELECT count(*) FROM run_events WHERE run_id = %s AND kind = 'failed'",
        (packet.run_id,),
    )
    assert failed == [(1,)]


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P2-07")
async def test_mid_session_register_keeps_the_session_protocol(
    db: DbUrls, fake_runner: FakeRunnerFactory
) -> None:
    """A second `register` on an open protocol-1 session that now offers protocol 2 is
    acked, but the runner row keeps protocol 1: the session cannot switch, and a row saying
    2 would route cancels to a `cancel` message this session never sends."""
    runner = fake_runner(profiles=["acme-site"], connect=False)
    assert runner.connect().protocol_version == 1

    runner.capabilities = list(V2_CAPABILITIES)
    again = runner.register_message([1, 2])
    runner.send(again)
    runner.wait_for(lambda r: again.message_id in r.acked)

    rows = _owner(db, "SELECT protocol_version FROM runners WHERE id = %s", (runner.runner_id,))
    assert rows == [(1,)]
