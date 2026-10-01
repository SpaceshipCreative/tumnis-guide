"""Ask the agent (P2-17, FR-2.5): the composer's toggle sends a question to
`POST /v1/projects/{id}/ask`, which makes an AI task and runs it on the project's agent
(R-23), so every conversation stays inside a task. The run's result is the answer: it
shows on the task and as a `result` review item."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest

from tests._mcp import http_for
from tumnis.modules.agents.tests.integration._runs import (
    owner_rows,
    relay,
    run_world,
    settle,
    wait_until,
)

if TYPE_CHECKING:
    from dbos import DBOS
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]

QUESTION = "Which typefaces does the brand guide name for headings?"
ANSWER = "Inter for headings and Source Serif for body text (Brand guide, page 4)."


@pytest.mark.req("FR-2.5")
@pytest.mark.wp("P2-17")
@pytest.mark.xfail(strict=True, reason="spec:P2-17")
async def test_ask_creates_ai_task_and_answer_lands_on_task_and_review(  # noqa: PLR0917
    app: FastAPI,
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-17-01
    Asking makes one task labeled AI with source `ask`, the question as its title, first
    action "Answer the question" and acceptance criteria "An answer with sources", and a
    run of it on the project's agent. The answer the agent posts is the result's summary:
    the task goes to In review, its result carries the answer, and a `result` review item
    shows it. An empty question is 422; a project without a ready agent is 409
    `no_ready_profile` and leaves no task behind."""
    world = await run_world(fake_runner, workspace, clock)
    async with relay(db):
        asked = await session_client.post(
            f"/v1/projects/{world.project_id}/ask", json={"question": QUESTION}
        )
        assert asked.status_code == 201, asked.text
        body = asked.json()
        task = body["task"]
        run_id = uuid.UUID(body["run_id"])
        assert task["project_id"] == str(world.project_id)
        assert (task["label"], task["source"], task["title"]) == ("ai", "ask", QUESTION)
        assert task["first_action"] == "Answer the question"
        assert task["acceptance_criteria"] == "An answer with sources"
        await settle()

        world.runner.wait_for(lambda r: any(m.run_id == run_id for m in r.runs()))
        assert await wait_until(
            lambda: (
                owner_rows(db, "SELECT status FROM runs WHERE id = %s", (run_id,)) == [("running",)]
            )
        )
        async with http_for(app, world.token(run_id)) as http:
            posted = await http.post(
                f"/v1/runs/{run_id}/result",
                json={"run_id": str(run_id), "outcome": "done", "summary": ANSWER},
                headers={"Idempotency-Key": f"ask-{uuid.uuid4()}"},
            )
        assert posted.status_code == 200, posted.text

    [(status,)] = owner_rows(db, "SELECT status::text FROM tasks WHERE id = %s", (task["id"],))
    assert status == "in_review"
    [(summary,)] = owner_rows(db, "SELECT summary FROM results WHERE task_id = %s", (task["id"],))
    assert summary == ANSWER
    review = await session_client.get("/v1/review", params={"kind": "result"})
    assert review.status_code == 200, review.text
    items: list[dict[str, Any]] = [
        i for i in review.json()["items"] if i["target_id"] == task["id"]
    ]
    assert len(items) == 1
    assert items[0]["payload"]["summary"] == ANSWER

    empty = await session_client.post(f"/v1/projects/{world.project_id}/ask", json={"question": ""})
    assert empty.status_code == 422, empty.text

    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    async with tenant_session(world.ctx) as s:
        bare = await projects.create_project(
            s, world.ctx.actor, projects.ProjectCreate(name="No agent yet"), now=clock.now()
        )
    refused = await session_client.post(f"/v1/projects/{bare.id}/ask", json={"question": QUESTION})
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "no_ready_profile"
    assert owner_rows(db, "SELECT count(*) FROM tasks WHERE project_id = %s", (bare.id,)) == [(0,)]
