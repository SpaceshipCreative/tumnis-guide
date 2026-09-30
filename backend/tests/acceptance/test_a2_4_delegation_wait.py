"""A2.4 · Delegation and wait_for_task (phase 2 acceptance, committed red on the phase's
first day).

The master delegates a task; `wait_for_task` returns `waiting_on_human` inside its timeout
while the child waits on a question, and `done` after the answer (design decision 6).
Turns green with P2-06.

Fixtures: `db`, `dbos`, `clock`, `fakes`, `app`, `workspace`, `fake_runner`,
`session_client`. The master's key (`agent_profiles.role = master`, scopes `tasks:read`,
`tasks:write`, `delegate`) is made by `_phase2.arrange_world(master=True)` in the
`workspace` fixture's workspace, as `key_client` would make it (the plan lists `seed` and
`key_client`). The test plays both agents over MCP: the master with its key, the child
with its run's task token.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from tests.acceptance._phase2 import (
    arrange_world,
    decide,
    human_wait_poll_seconds,
    post_result,
    relay,
    review_items,
    rows,
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
    pytest.mark.req("A2.4", "FR-5.2"),
]

QUESTION = "Should the footer link open in a new tab?"
WAIT_S = 30
ANSWERED_WITHIN_S = 5


@pytest.mark.wp("P2-06")
@pytest.mark.xfail(strict=True, reason="spec:P2-06")
async def test_master_delegates_and_wait_for_task_never_parks_on_a_human(  # noqa: PLR0917
    db: DbUrls,
    dbos: Any,
    clock: FixedClock,
    fakes: Any,
    app: FastAPI,
    workspace: WorkspaceHandle,
    fake_runner: FakeRunnerFactory,
    session_client: SessionClient,
) -> None:
    """A2.4
    Given the master's key and a task T in "Acme site", when the master delegates T, then
    the child run's DBOS workflow ID equals the delegation id and `delegations.depth` is 1;
    while the child waits on a question, `wait_for_task(timeout_seconds=30)` returns
    `waiting_on_human` with the question text under 5 s after the child asks; after the
    human answers and the child posts its result, `wait_for_task` returns `done` with
    `run_status="succeeded"` and the result summary.
    """
    human_wait_poll_seconds(2)
    world = await arrange_world(fake_runner, workspace, clock, master=True)
    master_key = world.master_key or ""
    task = await world.ai_task("Fix footer link")

    async with relay(db):
        # 1. The master delegates T over MCP.
        delegated = await tool(world.runner, master_key, "delegate_task", {"task_id": str(task)})
        assert delegated.ok, delegated
        delegation_id = uuid.UUID(delegated.data["delegation_id"])
        world.delivered(delegation_id)
        child_token = world.token(delegation_id)
        assert rows(db, "SELECT workflow_id FROM runs WHERE id = %s", delegation_id) == [
            {"workflow_id": str(delegation_id)}
        ]
        assert rows(db, "SELECT depth FROM delegations") == [{"depth": 1}]

        # 2. The master waits; the child asks a question meanwhile.
        waiting = asyncio.create_task(
            tool(
                world.runner,
                master_key,
                "wait_for_task",
                {"delegation_id": str(delegation_id), "timeout_seconds": WAIT_S},
            )
        )
        asked_at = time.monotonic()
        asked = await tool(
            world.runner,
            child_token,
            "ask_human",
            {"run_id": str(delegation_id), "prompt": QUESTION},
        )
        first = await waiting
        waited = time.monotonic() - asked_at
        assert first.ok, first
        assert first.data["status"] == "waiting_on_human"
        assert first.data["question"] == QUESTION
        assert waited < ANSWERED_WITHIN_S
        assert asked.ok, asked

        # 3. The human answers; the child picks the answer up and posts its result.
        [item] = await review_items(session_client, "question")
        answered = await decide(session_client, item, "answer", {"answer": "Yes"})
        assert answered.status_code == 200, answered.text

        async def child_answered() -> Any:
            again = await tool(
                world.runner,
                child_token,
                "ask_human",
                {
                    "run_id": str(delegation_id),
                    "prompt": QUESTION,
                    "question_id": wait_id(asked.data),
                },
            )
            return again if again.ok and again.data["status"] == "answered" else None

        assert await until(child_answered)
        posted = await post_result(
            world.runner, child_token, delegation_id, "The footer link opens in a new tab"
        )
        assert posted.ok, posted

        # 4. The master waits again: done.
        done = await tool(
            world.runner,
            master_key,
            "wait_for_task",
            {"delegation_id": str(delegation_id), "timeout_seconds": WAIT_S},
        )
    assert done.ok, done
    assert done.data["status"] == "done"
    assert done.data["run_status"] == "succeeded"
    assert done.data["result_summary"] == "The footer link opens in a new tab"
