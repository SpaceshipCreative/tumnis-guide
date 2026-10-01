"""The kill switch (P2-09, SAF-4): one control in the app, the API and (through the master)
the `pause_agents` tool pauses all dispatch and cancels the running runs through the
agent's adapter; queued runs are held until a person resumes in the app. Pause and resume
are audited; no key resumes anything.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest

from tumnis.modules.agents.tests.integration._runs import (
    audit_rows,
    call_tool,
    cancels,
    mark_outbox_sent,
    master_key,
    open_pauses,
    owner_rows,
    relay,
    run_world,
    step_names,
    wait_until,
    workflow_status,
)

if TYPE_CHECKING:
    from dbos import DBOS
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkerKillerFactory, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]

CANCEL_WITHIN_S = 5  # plan default


def _run(db: DbUrls, run_id: UUID) -> tuple[str, str | None]:
    [(status, reason)] = owner_rows(
        db, "SELECT status, stop_reason FROM runs WHERE id = %s", (run_id,)
    )
    return status, reason


def _sent_runs(db: DbUrls, run_id: UUID) -> int:
    [(count,)] = owner_rows(
        db,
        "SELECT count(*) FROM runner_messages WHERE direction = 'out' AND type = 'run'"
        " AND payload->>'run_id' = %s",
        (str(run_id),),
    )
    return int(count)


@pytest.mark.req("SAF-4")
@pytest.mark.wp("P2-09")
async def test_pause_from_app_cancels_running_runs_through_adapter(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-09-01
    Two runs are running; the user pauses all agents from the app. The answer counts the
    two running runs, and within 5 s both runs are `cancelled` with reason `killswitch`
    and the fake runner received exactly one `cancel` for each.
    """
    world = await run_world(fake_runner, workspace, clock)
    tasks = [await world.ai_task(f"Long task {n}") for n in (1, 2)]
    async with relay(db):
        runs = [await world.request(task.id) for task in tasks]
        assert await wait_until(lambda: len({m.run_id for m in world.runner.runs()}) == 2)

        paused = await session_client.post(
            "/v1/agents/pause", json={"scope": "workspace", "reason": "Something looks wrong"}
        )
        assert paused.status_code in {200, 201, 202}, paused.text
        body = paused.json()
        assert body["cancelled_runs"] == 2
        assert body["held_runs"] == 0
        UUID(body["pause_id"])

        def all_cancelled() -> bool:
            return all(_run(db, run_id)[0] == "cancelled" for run_id in runs)

        assert await wait_until(all_cancelled, timeout=CANCEL_WITHIN_S)
        for run_id in runs:
            assert _run(db, run_id) == ("cancelled", "killswitch")
            assert len(cancels(world.runner, run_id)) == 1


@pytest.mark.req("SAF-4")
@pytest.mark.wp("P2-09")
async def test_pause_from_api_with_master_key(  # noqa: PLR0917
    app: FastAPI,
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-09-02
    The master key pauses through the `pause_agents` tool and through its REST twin
    `POST /v1/agents/pause`: each opens a workspace pause and writes a `killswitch.on` row
    whose actor is the key. A key that holds `delegate` but is not the master's gets 403
    `master_only` on both doors and pauses nothing.
    """
    from tests._mcp import http_for  # noqa: PLC0415
    from tumnis.modules.auth import api as auth  # noqa: PLC0415

    world = await run_world(fake_runner, workspace, clock)
    master = await master_key(world)

    via_tool = await call_tool(
        world.runner,
        master,
        "pause_agents",
        {"scope": "workspace", "reason": "Stop all agents", "idempotency_key": "kill-tool-01"},
    )
    assert via_tool.ok, via_tool
    assert open_pauses(db) == [("workspace", None, "Stop all agents")]

    resumed = await session_client.post(
        "/v1/agents/resume", json={"scope": "workspace", "reason": "Checked"}
    )
    assert resumed.status_code in {200, 202, 204}, resumed.text
    assert open_pauses(db) == []

    async with http_for(app, master) as http:
        via_rest = await http.post(
            "/v1/agents/pause",
            json={"scope": "workspace", "reason": "Stop again"},
            headers={"Idempotency-Key": "kill-rest-01"},
        )
    assert via_rest.status_code in {200, 201, 202}, via_rest.text
    assert via_rest.json()["pause_id"] != via_tool.data["pause_id"]
    assert open_pauses(db) == [("workspace", None, "Stop again")]

    rows = audit_rows(db, "killswitch.on")
    assert [(r[0], r[2]) for r in rows] == [
        ("api_key", "Stop all agents"),
        ("api_key", "Stop again"),
    ]

    other = await auth.create_key(
        workspace.ctx,
        auth.KeyIn(name="not the master", scopes=["tasks:read", "delegate"]),
        now=clock.now(),
    )
    refused_tool = await call_tool(
        world.runner,
        other.key,
        "pause_agents",
        {"scope": "workspace", "reason": "Not mine", "idempotency_key": "kill-tool-02"},
    )
    assert refused_tool.code == "master_only", refused_tool
    async with http_for(app, other.key) as http:
        refused_rest = await http.post(
            "/v1/agents/pause",
            json={"scope": "workspace", "reason": "Not mine"},
            headers={"Idempotency-Key": "kill-rest-02"},
        )
    assert refused_rest.status_code == 403, refused_rest.text
    assert refused_rest.json()["code"] == "master_only"
    assert len(audit_rows(db, "killswitch.on")) == 2


@pytest.mark.req("SAF-4")
@pytest.mark.wp("P2-09")
async def test_dispatch_refused_and_queued_runs_held(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-09-03
    Two runs running and a third queued behind them (two per project): the pause holds the
    queued run at once (`held`, counted in the answer); after the running two end it stays
    `held`, its workflow parked, and nothing is sent to the runner for it. A new Run during
    the pause is 409 `agents_paused` and makes no run.
    """
    world = await run_world(fake_runner, workspace, clock)
    tasks = [await world.ai_task(f"Queue task {n}") for n in (1, 2, 3, 4)]
    async with relay(db):
        running = [await world.request(task.id) for task in tasks[:2]]
        assert await wait_until(
            lambda: all(_run(db, r)[0] == "running" for r in running), timeout=5
        )
        queued = await world.request(tasks[2].id)
        assert _run(db, queued)[0] == "queued"

        paused = await session_client.post(
            "/v1/agents/pause", json={"scope": "workspace", "reason": "Hold everything"}
        )
        assert paused.status_code in {200, 201, 202}, paused.text
        assert paused.json()["held_runs"] == 1
        assert paused.json()["cancelled_runs"] == 2
        assert _run(db, queued) == ("held", None)

        assert await wait_until(
            lambda: all(_run(db, r)[0] == "cancelled" for r in running), timeout=CANCEL_WITHIN_S
        )
        assert await wait_until(lambda: workflow_status(queued) == "PENDING", timeout=5)
        await asyncio.sleep(1)
        assert _run(db, queued) == ("held", None)
        assert world.packets(queued) == []
        assert _sent_runs(db, queued) == 0

        before = owner_rows(db, "SELECT count(*) FROM runs")
        refused = await session_client.post(f"/v1/tasks/{tasks[3].id}/run", json={})
        assert refused.status_code == 409, refused.text
        assert refused.json()["code"] == "agents_paused"
        assert owner_rows(db, "SELECT count(*) FROM runs") == before


@pytest.mark.req("SAF-4")
@pytest.mark.wp("P2-09")
async def test_pause_and_resume_audited(
    workspace: WorkspaceHandle,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-09-04
    A pause and a resume from the app each write one audit row (`killswitch.on`,
    `killswitch.off`) in their own transaction, with the user as actor, the reason given,
    the source address and the request's `X-Request-ID` as correlation ID.
    """
    paused = await session_client.post(
        "/v1/agents/pause",
        json={"scope": "workspace", "reason": "Runaway agent"},
        headers={"X-Request-ID": "req-kill-on-1"},
    )
    assert paused.status_code in {200, 201, 202}, paused.text
    resumed = await session_client.post(
        "/v1/agents/resume",
        json={"scope": "workspace", "reason": "Fixed the prompt"},
        headers={"X-Request-ID": "req-kill-off-1"},
    )
    assert resumed.status_code in {200, 202, 204}, resumed.text

    for action, reason, request_id in (
        ("killswitch.on", "Runaway agent", "req-kill-on-1"),
        ("killswitch.off", "Fixed the prompt", "req-kill-off-1"),
    ):
        [row] = audit_rows(db, action)
        actor_type, actor_id, got_reason, source_ip, correlation_id, _type, _id = row
        assert actor_type == "user"
        assert actor_id == workspace.user_id
        assert got_reason == reason
        assert source_ip
        assert correlation_id == request_id


@pytest.mark.req("SAF-4")
@pytest.mark.wp("P2-09")
async def test_no_key_can_resume(  # noqa: PLR0917
    app: FastAPI,
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-09-05
    With the workspace and a project paused, the master key on `POST /v1/agents/resume`
    and `POST /v1/projects/{id}/resume` gets 403 `session_required`, and both pauses stay
    open. No op in the agent surface's registry resumes anything (no tool or REST twin
    names a resume).
    """
    from tests._mcp import http_for  # noqa: PLC0415
    from tumnis.core import agent_surface  # noqa: PLC0415
    from tumnis.wiring import load_mcp  # noqa: PLC0415

    world = await run_world(fake_runner, workspace, clock)
    master = await master_key(world)
    for path, body in (
        ("/v1/agents/pause", {"scope": "workspace", "reason": "Stop"}),
        (f"/v1/projects/{world.project_id}/pause", {"reason": "Stop this one"}),
    ):
        paused = await session_client.post(path, json=body)
        assert paused.status_code in {200, 201, 202}, paused.text
    assert len(open_pauses(db)) == 2

    async with http_for(app, master) as http:
        for path, body in (
            ("/v1/agents/resume", {"scope": "workspace", "reason": "Go"}),
            (f"/v1/projects/{world.project_id}/resume", {"reason": "Go"}),
        ):
            refused = await http.post(path, json=body, headers={"Idempotency-Key": "resume-01"})
            assert refused.status_code == 403, (path, refused.text)
            assert refused.json()["code"] == "session_required", path
    assert len(open_pauses(db)) == 2
    assert audit_rows(db, "killswitch.off") == []

    load_mcp()
    ops: list[Any] = agent_surface.ops()
    assert "pause_agents" in [op.name for op in ops]
    for op in ops:
        assert "resume" not in op.name, op.name
        assert "resume" not in op.rest_path, op.rest_path


@pytest.mark.req("SAF-4")
@pytest.mark.wp("P2-09")
async def test_resume_releases_held_runs(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-09-06
    A run held by the pause, its workflow parked, starts after the user resumes in the
    app: it becomes `running` and the runner receives its `run` once.
    """
    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Held task")
    run_id = await world.request(task.id)  # no relay yet: the run waits in the outbox
    paused = await session_client.post(
        "/v1/agents/pause", json={"scope": "workspace", "reason": "Wait"}
    )
    assert paused.status_code in {200, 201, 202}, paused.text
    assert _run(db, run_id)[0] == "held"
    async with relay(db):
        # The relay skips the outbox rows written before it: start the held run's
        # workflow as its run.requested subscriber would.
        from tumnis.modules.agents import workflows  # noqa: PLC0415

        await workflows.start_dispatch(workspace.id, run_id, world.project_id, None)
        assert await wait_until(lambda: workflow_status(run_id) == "PENDING", timeout=5)
        await asyncio.sleep(0.5)
        assert _run(db, run_id)[0] == "held"
        assert world.packets(run_id) == []

        resumed = await session_client.post(
            "/v1/agents/resume", json={"scope": "workspace", "reason": "Go on"}
        )
        assert resumed.status_code in {200, 202, 204}, resumed.text
        assert await wait_until(lambda: _run(db, run_id)[0] == "running", timeout=10)
        world.runner.wait_for(lambda r: any(m.run_id == run_id for m in r.runs()))
        assert len(world.packets(run_id)) == 1


GUARD_POINTS = {
    "queued": None,  # paused before the run left the queue
    "after_prepare": "agents.dispatch_run.after_prepare",  # prepare_run recorded, not sent
}


@pytest.mark.req("SAF-4")
@pytest.mark.wp("P2-09")
@pytest.mark.slow
@pytest.mark.parametrize("point", list(GUARD_POINTS))
async def test_pause_guards_before_flip_and_before_send(  # noqa: PLR0917
    point: str,
    app: FastAPI,
    worker_killer: WorkerKillerFactory,
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P2-09-08 (queued, after_prepare)
    queued: the pause lands before a worker takes the run; the worker's `dispatch_run`
    runs `check_pause` and never `prepare_run`, and the run stays `held`. after_prepare:
    the worker is killed after `prepare_run` flipped the run to `running`, the pause lands,
    and the restarted worker's `send_to_agent` sees it: the run ends `cancelled` with
    reason `killswitch`. In both cases the runner never receives the run.
    """
    from tests.fixtures import KILLED_EXIT  # noqa: PLC0415
    from tumnis.core.clock import SystemClock  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415

    app.state.clock = SystemClock()
    killpoint = GUARD_POINTS[point]
    killer = worker_killer(killpoint or "agents.never", events=0)
    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Guarded task")
    mark_outbox_sent(db)
    run_id = await world.request(task.id)

    async def pause() -> None:
        await agents.pause(
            world.ctx,
            agents.PauseIn(scope="workspace", reason="Guard"),
            now=clock.now(),
        )

    if killpoint is None:
        await pause()
    else:
        armed = await killer.start(killpoint)
        try:
            code = await asyncio.wait_for(armed.wait(), 60)
        except TimeoutError:
            await killer.stop(armed)
            pytest.fail(f"worker not killed at {killpoint}\n{killer.log_tail()}")
        assert code == KILLED_EXIT, killer.log_tail()
        assert _run(db, run_id)[0] == "running"
        await pause()

    world.runner.heartbeat()
    worker = await killer.start(None)
    try:
        if killpoint is None:
            assert await wait_until(
                lambda: "check_pause" in step_names(killer.sys_db, run_id), timeout=30
            ), killer.log_tail()
            await asyncio.sleep(1)
            assert _run(db, run_id) == ("held", None)
            assert "prepare_run" not in step_names(killer.sys_db, run_id)
        else:
            assert await wait_until(lambda: _run(db, run_id)[0] == "cancelled", timeout=30), (
                killer.log_tail()
            )
            assert _run(db, run_id) == ("cancelled", "killswitch")
    finally:
        await killer.stop(worker)

    assert world.packets(run_id) == []
    assert _sent_runs(db, run_id) == 0
