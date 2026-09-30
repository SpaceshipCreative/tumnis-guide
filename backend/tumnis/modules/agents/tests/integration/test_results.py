"""Result intake and result review (P2-04, FR-5.8).

The agent posts its result with its task token (`POST /v1/runs/{id}/result`, the
`post_result` tool's twin): the result is stored, the task goes to In review and a
`result` review item waits for the human. Accept finishes the task; reject sends it back
with the feedback as a comment, and the agent runs again with that comment in its packet.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest

from tests._mcp import http_for
from tumnis.modules.agents.tests.integration._runs import (
    RESULT_OUTPUT,
    RunWorld,
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
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]

FEEDBACK = "Link should open in a new tab"


def _body(run_id: UUID) -> dict[str, Any]:
    return {"run_id": str(run_id), **RESULT_OUTPUT}


async def _post_result(app: FastAPI, token: str, run_id: UUID, body: dict[str, Any]) -> Any:
    async with http_for(app, token) as http:
        return await http.post(
            f"/v1/runs/{run_id}/result",
            json=body,
            headers={"Idempotency-Key": f"result-{uuid.uuid4()}"},
        )


async def _started(world: RunWorld, db: DbUrls, task_id: UUID) -> UUID:
    run_id = await world.request(task_id)
    world.runner.wait_for(lambda r: any(m.run_id == run_id for m in r.runs()))
    assert await wait_until(
        lambda: owner_rows(db, "SELECT status FROM runs WHERE id = %s", (run_id,)) == [("running",)]
    )
    return run_id


def _task_status(db: DbUrls, task_id: UUID) -> str:
    [(status,)] = owner_rows(db, "SELECT status::text FROM tasks WHERE id = %s", (task_id,))
    return str(status)


def _result_item(db: DbUrls, task_id: UUID) -> tuple[UUID, int, dict[str, Any]]:
    [(item_id, version, payload)] = owner_rows(
        db,
        "SELECT id, version, payload FROM review_items WHERE kind = 'result'"
        " AND target_type = 'task' AND target_id = %s AND decided_at IS NULL",
        (task_id,),
    )
    return item_id, version, payload


@pytest.mark.req("FR-5.8")
@pytest.mark.wp("P2-04")
@pytest.mark.xfail(strict=True, reason="spec:P2-04")
async def test_result_moves_task_to_in_review_with_details(  # noqa: PLR0917
    app: FastAPI,
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P2-04-09
    The result the agent posts with its task token is stored with its summary, files
    touched and links; the task moves to In review, a `result` review item shows them, and
    the run ends `succeeded`. Posting it again returns the first result.
    """
    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")
    async with relay(db):
        run_id = await _started(world, db, task.id)
        token = world.token(run_id)
        posted = await _post_result(app, token, run_id, _body(run_id))
        assert posted.status_code == 200, posted.text
        result = posted.json()
        assert result["run_id"] == str(run_id)
        assert result["task_id"] == str(task.id)
        assert result["summary"] == RESULT_OUTPUT["summary"]

        again = await _post_result(app, token, run_id, _body(run_id))
        assert again.status_code == 200, again.text
        assert again.json()["id"] == result["id"]

        assert await wait_until(
            lambda: (
                owner_rows(db, "SELECT status FROM runs WHERE id = %s", (run_id,))
                == [("succeeded",)]
            )
        )

    [(summary, files, links)] = owner_rows(
        db, "SELECT summary, files_touched, links FROM results WHERE run_id = %s", (run_id,)
    )
    assert summary == RESULT_OUTPUT["summary"]
    assert files == RESULT_OUTPUT["files_touched"]
    assert [link["url"] for link in links] == [link["url"] for link in RESULT_OUTPUT["links"]]
    assert _task_status(db, task.id) == "in_review"
    _item, _version, payload = _result_item(db, task.id)
    assert payload["summary"] == RESULT_OUTPUT["summary"]
    assert payload["run_id"] == str(run_id)
    assert [f["path"] for f in payload["files_touched"]] == ["src/footer.tsx"]
    assert len(payload["links"]) == 2
    assert owner_rows(
        db,
        "SELECT count(*) FROM outbox WHERE name = 'result.posted' AND payload->>'run_id' = %s",
        (str(run_id),),
    ) == [(1,)]


@pytest.mark.req("FR-5.8")
@pytest.mark.wp("P2-04")
@pytest.mark.xfail(strict=True, reason="spec:P2-04")
async def test_accept_moves_task_to_done(  # noqa: PLR0917
    app: FastAPI,
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-04-10
    Accepting the `result` item closes it, emits `human.decided` (kind `result`, decision
    `accept`) and moves the task to Done.
    """
    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")
    async with relay(db):
        run_id = await _started(world, db, task.id)
        posted = await _post_result(app, world.token(run_id), run_id, _body(run_id))
        assert posted.status_code == 200, posted.text
        item_id, version, _payload = _result_item(db, task.id)

        decided = await session_client.post(
            f"/v1/review/{item_id}/decide", json={"action": "accept", "version": version}
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["decision"] == "accept"
        assert await wait_until(lambda: _task_status(db, task.id) == "done")

    assert owner_rows(
        db,
        "SELECT payload->>'item_kind', payload->>'decision' FROM outbox"
        " WHERE name = 'human.decided' AND payload->>'item_id' = %s",
        (str(item_id),),
    ) == [("result", "accept")]
    assert owner_rows(
        db, "SELECT decided_at IS NOT NULL FROM review_items WHERE id = %s", (item_id,)
    ) == [(True,)]


@pytest.mark.req("FR-5.8")
@pytest.mark.wp("P2-04")
@pytest.mark.xfail(strict=True, reason="spec:P2-04")
async def test_reject_adds_comment_and_returns_to_agent(  # noqa: PLR0917
    app: FastAPI,
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-04-11
    Rejecting the `result` item needs feedback; the feedback becomes a comment on the task,
    the task returns to In progress, and a new run of the task starts (`rerun_of` the
    first) whose packet carries the comment.
    """
    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")
    async with relay(db):
        first = await _started(world, db, task.id)
        posted = await _post_result(app, world.token(first), first, _body(first))
        assert posted.status_code == 200, posted.text
        assert await wait_until(
            lambda: (
                owner_rows(db, "SELECT status FROM runs WHERE id = %s", (first,))
                == [("succeeded",)]
            )
        )
        item_id, version, _payload = _result_item(db, task.id)

        bare = await session_client.post(
            f"/v1/review/{item_id}/decide", json={"action": "reject", "version": version}
        )
        assert bare.status_code == 422, bare.text

        rejected = await session_client.post(
            f"/v1/review/{item_id}/decide",
            json={"action": "reject", "payload": {"feedback": FEEDBACK}, "version": version},
        )
        assert rejected.status_code == 200, rejected.text

        def rerun() -> list[tuple[Any, ...]]:
            return owner_rows(
                db, "SELECT id FROM runs WHERE task_id = %s AND rerun_of = %s", (task.id, first)
            )

        assert await wait_until(lambda: len(rerun()) == 1)
        [(second,)] = rerun()
        world.runner.wait_for(lambda r: any(m.run_id == second for m in r.runs()))

    assert _task_status(db, task.id) == "in_progress"
    comments = owner_rows(db, "SELECT body_md FROM task_comments WHERE task_id = %s", (task.id,))
    assert [body for (body,) in comments] == [FEEDBACK]
    [packet] = world.packets(second)
    assert FEEDBACK in json.dumps(packet)
    assert FEEDBACK not in json.dumps(world.packets(first))


@pytest.mark.req("FR-5.8")
@pytest.mark.wp("P2-04")
@pytest.mark.xfail(strict=True, reason="spec:P2-04")
async def test_result_with_other_runs_token_refused(  # noqa: PLR0917
    app: FastAPI,
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P2-04-12
    A result for run B posted with run A's task token is 403 `run_mismatch`; nothing is
    stored and B's task stays In progress.
    """
    world = await run_world(fake_runner, workspace, clock)
    task_a = await world.ai_task("Fix footer link")
    task_b = await world.ai_task("Fix header logo")
    async with relay(db):
        run_a = await _started(world, db, task_a.id)
        run_b = await _started(world, db, task_b.id)
        refused = await _post_result(app, world.token(run_a), run_b, _body(run_b))
        assert refused.status_code == 403, refused.text
        assert refused.json()["code"] == "run_mismatch"

    assert owner_rows(db, "SELECT count(*) FROM results WHERE run_id = %s", (run_b,)) == [(0,)]
    assert _task_status(db, task_b.id) == "in_progress"
