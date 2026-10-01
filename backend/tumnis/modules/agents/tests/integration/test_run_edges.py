"""Edges of the run lifecycle beyond the spec tests (P2-04, review follow-ups).

- A result posted while the run waits on a human still ends the run `succeeded` (the
  wait ends first; the state table has no direct waiting -> succeeded edge).
- Rejecting a result keeps the feedback comment and the move back to In progress when the
  task can no longer run (here its label became `human`): no rerun, nothing rolled back.
- A run whose task went to the trash while queued is refused (`task_not_found`), not
  retried.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest

from tests._mcp import http_for
from tumnis.modules.agents.tests.integration._runs import (
    RESULT_OUTPUT,
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


def _run_status(db: DbUrls, run_id: UUID) -> str | None:
    rows = owner_rows(db, "SELECT status FROM runs WHERE id = %s", (run_id,))
    return rows[0][0] if rows else None


def _task_status(db: DbUrls, task_id: UUID) -> str:
    [(status,)] = owner_rows(db, "SELECT status::text FROM tasks WHERE id = %s", (task_id,))
    return str(status)


async def _post_result(app: FastAPI, token: str, run_id: UUID) -> Any:
    async with http_for(app, token) as http:
        return await http.post(
            f"/v1/runs/{run_id}/result",
            json={"run_id": str(run_id), **RESULT_OUTPUT},
            headers={"Idempotency-Key": f"result-{uuid.uuid4()}"},
        )


@pytest.mark.wp("P2-04")
async def test_result_while_waiting_on_human_succeeds(  # noqa: PLR0917
    app: FastAPI,
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    from tumnis.modules.agents import api as agents  # noqa: PLC0415

    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")
    async with relay(db):
        run_id = await world.request(task.id)
        world.runner.wait_for(lambda r: any(m.run_id == run_id for m in r.runs()))
        await agents.signal_run(world.ctx, run_id, "waiting")
        assert await wait_until(lambda: _run_status(db, run_id) == "waiting_on_human")

        posted = await _post_result(app, world.token(run_id), run_id)
        assert posted.status_code == 200, posted.text
        assert await wait_until(lambda: _run_status(db, run_id) == "succeeded")

    assert _task_status(db, task.id) == "in_review"
    assert owner_rows(
        db,
        "SELECT count(*) FROM outbox WHERE name = 'run.finished' AND payload->>'run_id' = %s",
        (str(run_id),),
    ) == [(1,)]


@pytest.mark.wp("P2-04")
async def test_reject_kept_when_the_rerun_is_refused(  # noqa: PLR0917
    app: FastAPI,
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")
    async with relay(db):
        run_id = await world.request(task.id)
        world.runner.wait_for(lambda r: any(m.run_id == run_id for m in r.runs()))
        assert await wait_until(lambda: _run_status(db, run_id) == "running")
        posted = await _post_result(app, world.token(run_id), run_id)
        assert posted.status_code == 200, posted.text
        assert await wait_until(lambda: _run_status(db, run_id) == "succeeded")

        async with tenant_session(world.ctx) as s:
            current = await tasks.get_task(s, task.id)
            await tasks.update_task(
                s,
                world.ctx.actor,
                task.id,
                tasks.TaskPatch(label="human", estimate_minutes=30, version=current.version),
                current.version,
                now=clock.now(),
            )
        [(item_id, version)] = owner_rows(
            db,
            "SELECT id, version FROM review_items WHERE kind = 'result' AND target_id = %s"
            " AND decided_at IS NULL",
            (task.id,),
        )
        rejected = await session_client.post(
            f"/v1/review/{item_id}/decide",
            json={
                "action": "reject",
                "payload": {"feedback": "Use the new URL"},
                "version": version,
            },
        )
        assert rejected.status_code == 200, rejected.text
        assert await wait_until(lambda: _task_status(db, task.id) == "in_progress")

    comments = owner_rows(db, "SELECT body_md FROM task_comments WHERE task_id = %s", (task.id,))
    assert comments == [("Use the new URL",)]
    assert owner_rows(db, "SELECT count(*) FROM runs WHERE task_id = %s", (task.id,)) == [(1,)]


@pytest.mark.wp("P2-04")
async def test_task_trashed_while_queued_refuses_the_run(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """A run whose task went to the trash while it was queued: `prepare_run` answers a
    refusal (`task_not_found`), which `dispatch_run` ends as `failed`, instead of raising
    and retrying the step."""
    from tumnis.modules.agents import workflows  # noqa: PLC0415

    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")
    run_id = await world.request(task.id)  # no relay beside the test: the run stays queued
    trashed = owner_rows(
        db, "UPDATE tasks SET deleted_at = now() WHERE id = %s RETURNING id", (task.id,)
    )
    assert trashed == [(task.id,)]

    prepared = await workflows.prepare_run(str(workspace.id), str(run_id))

    assert prepared.refusal == "task_not_found"
    assert _run_status(db, run_id) == "queued"
