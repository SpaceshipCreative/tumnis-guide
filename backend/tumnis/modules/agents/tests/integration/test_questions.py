"""Questions mid-run (P2-05, FR-5.7): `ask_human` parks the run and its task on the
human; the answer from the review queue resumes them, across a killed worker too.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.agents.tests.integration._human import (
    ask,
    decide,
    human_waits,
    open_items,
    outbox_count,
    run_status,
    started,
    task_status,
)
from tumnis.modules.agents.tests.integration._runs import (
    finish,
    mark_outbox_sent,
    owner_rows,
    relay,
    run_world,
    wait_until,
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

QUESTION = "Which footer color?"


@pytest.mark.req("FR-5.7")
@pytest.mark.wp("P2-05")
async def test_ask_human_moves_task_to_waiting_and_adds_review_item(  # noqa: PLR0917
    app: FastAPI,
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P2-05-01
    The agent asks with its task token: the answer is `pending` with the question's id;
    the task is Waiting on human with one `question` review item carrying the prompt and
    the choices; one `question.asked`; the run is parked (`waiting_on_human`).
    """
    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")
    with human_waits(poll_seconds=1):
        async with relay(db):
            run_id = await started(world, db, task.id)
            asked = await ask(app, world.token(run_id), run_id, QUESTION, choices=["Navy", "Teal"])
            assert asked.status_code == 200, asked.text
            body = asked.json()
            assert body["status"] == "pending"
            assert body["id"]
            assert task_status(db, task.id) == "waiting_on_human"
            [item] = open_items(db, "question")
            assert item["payload"]["prompt"] == QUESTION
            assert item["payload"]["choices"] == ["Navy", "Teal"]
            assert item["target_type"] == "task"
            assert item["target_id"] == task.id
            assert outbox_count(db, "question.asked", run_id=str(run_id)) == 1
            assert await wait_until(lambda: run_status(db, run_id) == "waiting_on_human")


@pytest.mark.req("FR-5.7")
@pytest.mark.wp("P2-05")
async def test_answer_resumes_run(  # noqa: PLR0917
    app: FastAPI,
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-05-02
    The human answers "Navy": the task is back In progress (once), the run is running
    again, the re-sent `ask_human` (with the question id) returns `answered` with "Navy"
    at once, and the active clock runs again: the run still finishes on its result.
    """
    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")
    with human_waits(poll_seconds=1):
        async with relay(db):
            run_id = await started(world, db, task.id)
            token = world.token(run_id)
            asked = await ask(app, token, run_id, QUESTION)
            question_id = asked.json()["id"]
            assert await wait_until(lambda: run_status(db, run_id) == "waiting_on_human")

            [item] = open_items(db, "question")
            answered = await decide(session_client, item, "answer", {"answer": "Navy"})
            assert answered.status_code == 200, answered.text

            assert await wait_until(lambda: run_status(db, run_id) == "running")
            assert task_status(db, task.id) == "in_progress"
            assert (
                outbox_count(db, "task.status_changed", task_id=str(task.id), to="in_progress") == 2
            )

            loop = asyncio.get_running_loop()
            began = loop.time()
            again = await ask(app, token, run_id, QUESTION, question_id=question_id)
            assert loop.time() - began < 1.0
            assert again.status_code == 200, again.text
            assert again.json()["status"] == "answered"
            assert again.json()["answer"] == "Navy"
            assert again.json()["id"] == question_id

            finish(world.runner, run_id)
            assert await wait_until(lambda: run_status(db, run_id) == "succeeded")


@pytest.mark.req("FR-5.7")
@pytest.mark.wp("P2-05")
@pytest.mark.slow
async def test_killed_worker_during_wait_answer_still_resumes(  # noqa: PLR0917
    app: FastAPI,
    worker_killer: WorkerKillerFactory,
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-05-14
    The worker is killed while `question_flow` waits for the answer; a fresh worker
    recovers it, and the human's answer resumes the task and the run exactly once.
    """
    from tests.fixtures import KILLED_EXIT  # noqa: PLC0415
    from tumnis.core.clock import SystemClock  # noqa: PLC0415

    app.state.clock = SystemClock()  # the worker's runner sweep reads the real clock
    point = "agents.question_flow.waiting"
    killer = worker_killer(point, events=0)
    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")
    mark_outbox_sent(db)
    run_id = await world.request(task.id)

    armed = await killer.start(point)
    try:
        world.runner.wait_for(lambda r: any(m.run_id == run_id for m in r.runs()), timeout=30)
        with human_waits(poll_seconds=1):
            asked = await ask(app, world.token(run_id), run_id, QUESTION)
        assert asked.status_code == 200, asked.text
        question_id = asked.json()["id"]
        code = await asyncio.wait_for(armed.wait(), 60)
    except TimeoutError:
        await killer.stop(armed)
        pytest.fail(f"worker not killed at {point}\n{killer.log_tail()}")
    assert code == KILLED_EXIT, killer.log_tail()

    world.runner.heartbeat()
    worker = await killer.start(None)
    try:
        assert await wait_until(lambda: run_status(db, run_id) == "waiting_on_human", timeout=30)
        [item] = open_items(db, "question")
        answered = await decide(session_client, item, "answer", {"answer": "Navy"})
        assert answered.status_code == 200, answered.text
        assert await wait_until(lambda: run_status(db, run_id) == "running", timeout=60), (
            killer.log_tail()
        )
        assert task_status(db, task.id) == "in_progress"
        with human_waits(poll_seconds=1):
            again = await ask(app, world.token(run_id), run_id, QUESTION, question_id=question_id)
        assert again.json()["status"] == "answered"
        finish(world.runner, run_id)
        assert await wait_until(lambda: run_status(db, run_id) == "succeeded", timeout=60)
    finally:
        await killer.stop(worker)

    assert outbox_count(db, "task.status_changed", task_id=str(task.id), to="in_progress") == 2
    assert owner_rows(
        db,
        "SELECT count(*) FROM outbox WHERE name = 'run.finished' AND payload->>'run_id' = %s",
        (str(run_id),),
    ) == [(1,)]
