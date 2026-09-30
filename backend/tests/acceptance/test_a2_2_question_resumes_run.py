"""A2.2 · A question mid-run resumes the run (phase 2 acceptance, API level; committed red
on the phase's first day). The UI path is frontend/e2e/acceptance/A2.2-question.spec.ts.

An agent asks the human a question mid-run; the task waits on the human; the answer
resumes the run. Turns green with P2-05.

Fixtures: `db`, `dbos`, `clock`, `fakes`, `app`, `workspace`, `fake_runner`,
`session_client`. The plan lists `seed` and `task_token_client`: the task token here is
the one the run's packet carried (what an agent holds), and `_phase2.arrange_world` makes
the plan's rows in the `workspace` fixture's workspace, which `session_client` and
`fake_runner` serve. The test plays the agent: one log line, `ask_human`, then the
re-send and `post_result`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tests.acceptance._phase2 import (
    arrange_world,
    decide,
    human_wait_poll_seconds,
    outbox,
    post_result,
    relay,
    result_summary,
    review_items,
    run_status,
    run_task,
    stream,
    task_status,
    tool,
    until,
    wait_id,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.req("A2.2", "FR-5.7"),
]

QUESTION = "Which footer color?"


@pytest.mark.wp("P2-05")
@pytest.mark.xfail(strict=True, reason="spec:P2-05")
async def test_question_mid_run_waits_on_human_and_answer_resumes_run(  # noqa: PLR0917
    db: DbUrls,
    dbos: Any,
    clock: FixedClock,
    fakes: Any,
    app: FastAPI,
    workspace: WorkspaceHandle,
    fake_runner: FakeRunnerFactory,
    session_client: SessionClient,
) -> None:
    """A2.2
    Given an AI task and a long poll of 2 s, when the agent asks "Which footer color?"
    after one log line, then `ask_human` returns `pending` with the question id, and the
    task and the run are `waiting_on_human`; when the human answers "Navy" from the
    review queue, the task is `in_progress`, one `human.decided` exists and the run is
    running again; the re-sent `ask_human` returns `answered` with "Navy"; the final result
    summary contains "Navy"; exactly one `question.asked` and one `run.finished`.
    """
    human_wait_poll_seconds(2)
    world = await arrange_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")

    async with relay(db):
        # 1. Run the task.
        run_id = await run_task(session_client, task)
        await world.delivered(run_id)
        token = world.token(run_id)

        # 2. One log line, then the question; the call long-polls and returns pending.
        stream(world.runner, run_id, 1, "Reading the footer component")
        asked = await tool(
            world.runner, token, "ask_human", {"run_id": str(run_id), "prompt": QUESTION}
        )
        assert asked.ok, asked
        assert asked.data["status"] == "pending"
        question_id = wait_id(asked.data)
        assert await task_status(session_client, task) == "waiting_on_human"
        assert await run_status(session_client, run_id) == "waiting_on_human"

        # 3. The human answers from the review queue (R-04).
        [item] = await review_items(session_client, "question")
        answered = await decide(session_client, item, "answer", {"answer": "Navy"})
        assert answered.status_code == 200, answered.text

        async def resumed() -> bool:
            return await run_status(session_client, run_id) == "running"

        assert await until(resumed)
        assert await task_status(session_client, task) == "in_progress"
        assert len(outbox(db, "human.decided")) == 1

        # 4. The re-send returns the answer; the agent posts a result that echoes it.
        again = await tool(
            world.runner,
            token,
            "ask_human",
            {"run_id": str(run_id), "prompt": QUESTION, "question_id": question_id},
        )
        assert again.ok, again
        assert again.data["status"] == "answered"
        assert again.data["answer"] == "Navy"
        posted = await post_result(
            world.runner, token, run_id, f"Footer colour set to {again.data['answer']}"
        )
        assert posted.ok, posted

        assert await until(lambda: outbox(db, "run.finished", run_id))

    assert "Navy" in (result_summary(db, run_id) or "")
    assert len(outbox(db, "question.asked")) == 1
    assert len(outbox(db, "run.finished", run_id)) == 1
