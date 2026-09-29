"""The phase 1 run workflow (P1-04, FR-5.11, FR-14.6): `run_skill` dispatches through the
mailbox, waits on DBOS.recv for the runner's result and finishes exactly once, even when
the result arrives twice or the worker dies mid-dispatch."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._pg import APP, OWNER
from tumnis.modules.agents.tests.contract.base import SCRIPTED_OUTPUT, SKILL, make_packet

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import AppFactory, WorkerKillerFactory, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]


def _owner(db: DbUrls, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        return conn.execute(query.encode(), params).fetchall()


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P1-04")
@pytest.mark.xfail(strict=True, reason="spec:P1-04")
async def test_result_reaches_workflow_once(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-04-12
    The fake runner sends the same `result` twice (same `message_id`): both copies are
    acked, one `run_events` result row and one inbound mailbox row exist, and the workflow
    completes once with the scripted JSON.
    """
    from tumnis.modules.agents import workflows  # noqa: PLC0415

    runner = fake_runner(profiles=["acme-site"])
    runner.script("acme-site", SKILL, SCRIPTED_OUTPUT, repeat=2)
    profile_id = fake_runner.register_profile("acme-site", runner=runner)
    packet = make_packet(profile_id)

    handle = await workflows.start_run_skill(workspace.id, packet)
    outcome = await asyncio.wait_for(handle.get_result(), 20)
    assert outcome == {
        "run_id": str(packet.run_id),
        "status": "succeeded",
        "output_json": SCRIPTED_OUTPUT,
        "error": None,
    }

    results = [m for m in runner.sent if m.type == "result"]
    assert len(results) == 2
    assert len({m.message_id for m in results}) == 1
    await asyncio.to_thread(runner.wait_for, lambda r: results[0].message_id in r.acked)

    events = _owner(
        db,
        "SELECT kind FROM run_events WHERE run_id = %s ORDER BY created_at, kind",
        (packet.run_id,),
    )
    assert sorted(kind for (kind,) in events) == ["dispatched", "result"]
    inbound = _owner(
        db,
        "SELECT count(*) FROM runner_messages WHERE message_id = %s AND direction = 'in'",
        (results[0].message_id,),
    )
    assert inbound == [(1,)]
    run = _owner(db, "SELECT status, output FROM runs WHERE id = %s", (packet.run_id,))
    assert run == [("succeeded", SCRIPTED_OUTPUT)]
    workflows_run = await asyncio.to_thread(
        dbos.list_workflows, workflow_ids=[handle.get_workflow_id()]
    )
    assert [w.status for w in workflows_run] == ["SUCCESS"]


@pytest.mark.req("FR-14.6")
@pytest.mark.wp("P1-04")
@pytest.mark.slow
@pytest.mark.xfail(strict=True, reason="spec:P1-04")
async def test_kill_worker_mid_run_resumes(
    worker_killer: WorkerKillerFactory,
    app_factory: AppFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P1-04-13
    A worker killed at `agents.dispatch_step` right after the mailbox insert committed
    leaves one `run` mailbox row. The restarted worker recovers the workflow, re-runs the
    step (the deterministic message id writes nothing new), receives the runner's one
    result and finishes once.
    """
    from dbos import DBOSClient  # noqa: PLC0415

    from tests.fakes.fake_runner import (  # noqa: PLC0415
        FakeRunner,
        create_runner,
        make_test_client,
        register_profile,
    )
    from tests.fixtures import KILLED_EXIT  # noqa: PLC0415
    from tumnis.core.clock import SystemClock  # noqa: PLC0415
    from tumnis.modules.agents import workflows  # noqa: PLC0415

    killer = worker_killer("agents.dispatch_step", events=0)
    app = app_factory(dbos_system_database_url=killer.sys_db.url(APP))
    # The worker's runner sweep runs each minute on the real clock: the api stamps the
    # runner's heartbeats on it too, so a sweep during the test finds the runner online.
    app.state.clock = SystemClock()
    runner_id, token = create_runner(workspace, clock, "homelab-hermes")
    profile_id = register_profile(workspace, clock, "acme-site", runner_id=runner_id)
    packet = make_packet(profile_id)
    client = DBOSClient(system_database_url=killer.sys_db.url(APP))
    try:
        with make_test_client(app) as http:
            runner = FakeRunner(
                http, token, runner_id, name="homelab-hermes", profiles=["acme-site"], clock=clock
            )
            runner.script("acme-site", SKILL, SCRIPTED_OUTPUT)
            runner.connect()
            try:
                workflow_id = await workflows.enqueue_run_skill(client, workspace.id, packet)
                assert await killer.run_until_killed() == KILLED_EXIT, killer.log_tail()
                mailbox = _owner(
                    db,
                    "SELECT count(*) FROM runner_messages WHERE direction = 'out' AND type = 'run'"
                    " AND payload->>'run_id' = %s",
                    (str(packet.run_id),),
                )
                assert mailbox == [(1,)]

                worker = await killer.start(None)
                try:
                    handle: Any = await client.retrieve_workflow_async(workflow_id)
                    outcome = await asyncio.wait_for(handle.get_result(), 30)
                finally:
                    await killer.stop(worker)
            finally:
                runner.disconnect()
    finally:
        client.destroy()

    assert outcome["status"] == "succeeded", killer.log_tail()
    assert outcome["output_json"] == SCRIPTED_OUTPUT
    mailbox = _owner(
        db,
        "SELECT count(*) FROM runner_messages WHERE direction = 'out' AND type = 'run'"
        " AND payload->>'run_id' = %s",
        (str(packet.run_id),),
    )
    assert mailbox == [(1,)]
    results = _owner(
        db,
        "SELECT count(*) FROM run_events WHERE run_id = %s AND kind = 'result'",
        (packet.run_id,),
    )
    assert results == [(1,)]
    assert len(runner.runs()) == 1
    assert _owner(db, "SELECT status FROM runs WHERE id = %s", (packet.run_id,)) == [("succeeded",)]
