"""The scripted fake runner asks the human (Scott decision 55, R-37, A2.2's harness).

A task script's `{"ask_human": {"prompt", "choices"?}}` step goes through
`agents.api.ask_human` as the run's agent, so the task waits on the human with a
`question` review item; the fake waits for the answer and for the run to resume, then
plays on. A later result's summary has `{answer}` replaced by the human's answer.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.agents.tests.integration._human import (
    decide,
    open_items,
    outbox_count,
    task_status,
)
from tumnis.modules.agents.tests.integration._runs import owner_rows, relay, user_ctx, wait_until
from tumnis.modules.agents.tests.integration.test_fake_runner_playback import (
    TITLE,
    _ai_task,
    _project_with_fake_agent,
    _run_status,
)

if TYPE_CHECKING:
    from dbos import DBOS
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import Fakes, PepperFile, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

QUESTION = "Which footer color?"
SCRIPT: dict[str, Any] = {
    "task_title": TITLE,
    "runs": [
        [
            {"stream": {"kind": "log", "text": "Reading the footer component"}},
            {"ask_human": {"prompt": QUESTION, "choices": ["Navy", "Teal"]}},
            {"result": {"outcome": "done", "summary": "Footer colour set to {answer}"}},
        ]
    ],
}


@pytest.fixture
def scripted(
    fakes: Fakes, pepper_file: PepperFile, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Stored scripts on (as the worker enables them with fakes), played without pauses."""
    from tumnis.core import fake_scripts  # noqa: PLC0415
    from tumnis.modules.agents import fake_play  # noqa: PLC0415

    monkeypatch.setattr(fake_play, "STEP_PAUSE_S", 0.0)
    monkeypatch.setattr(fake_play, "RESULT_HOLD_S", 0.0)
    monkeypatch.setattr(fake_play, "QUESTION_POLL_S", 0.05)
    fake_scripts.enable()
    try:
        yield
    finally:
        fake_scripts.disable()


@pytest.mark.req("FR-5.7", "FR-5.5")
@pytest.mark.wp("P2-04")
async def test_scripted_fake_asks_the_human_and_posts_the_answer(  # noqa: PLR0917
    app: FastAPI,
    dbos: type[DBOS],
    scripted: None,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """The scripted run asks "Which footer color?" through `ask_human`: one
    `question.asked`, one open `question` review item with the prompt and the choices,
    the task and the run waiting on the human, and no result yet. The human answers
    "Navy": the run resumes and the fake posts its result, whose summary reads "Footer
    colour set to Navy"; the run ends `succeeded` and the task is In review."""
    from tumnis.core import fake_scripts  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415
    from tumnis.modules.agents.adapters.fake import parse_runner_script  # noqa: PLC0415

    key, stored = parse_runner_script(SCRIPT)
    await fake_scripts.put("runner", key, stored)
    project_id = await _project_with_fake_agent(workspace, clock)
    task_id = await _ai_task(workspace, clock, project_id)

    async with relay(db):
        run_id = await agents.request_run(task_id, agents.RunKind.TASK, ctx=user_ctx(workspace))
        assert await wait_until(lambda: _run_status(db, run_id) == "waiting_on_human", timeout=30)
        assert task_status(db, task_id) == "waiting_on_human"
        assert outbox_count(db, "question.asked", run_id=str(run_id)) == 1
        [item] = open_items(db, "question")
        assert item["payload"]["prompt"] == QUESTION
        assert item["payload"]["choices"] == ["Navy", "Teal"]
        assert owner_rows(db, "SELECT count(*) FROM results WHERE run_id = %s", (run_id,)) == [(0,)]

        answered = await decide(session_client, item, "answer", {"answer": "Navy"})
        assert answered.status_code == 200, answered.text
        assert await wait_until(lambda: _run_status(db, run_id) == "succeeded", timeout=30)

    assert owner_rows(db, "SELECT summary FROM results WHERE run_id = %s", (run_id,)) == [
        ("Footer colour set to Navy",)
    ]
    assert task_status(db, task_id) == "in_review"
    assert outbox_count(db, "question.asked", run_id=str(run_id)) == 1
