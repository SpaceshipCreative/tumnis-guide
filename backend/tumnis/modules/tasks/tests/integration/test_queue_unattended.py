"""Queueing a task for the unattended window (P4-04, FR-4.5): `PUT /v1/tasks/{id}/unattended
{queued}` sets or clears the task's queue flag (`unattended_queued_at`, who queued it).
Only AI tasks can be queued: Human and Hybrid answer 422 `not_ai` (and a pending label too).
Unqueueing works for any task, and a repeated PUT changes nothing."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests._auth import SessionClient
    from tests.fixtures import WorkspaceHandle
    from tumnis.modules.tasks.tests.conftest import MakeTask, SetStatus

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _path(task_id: object) -> str:
    return f"/v1/tasks/{task_id}/unattended"


@pytest.mark.req("FR-4.5")
@pytest.mark.wp("P4-04")
async def test_only_ai_tasks_can_be_queued(
    make_task: MakeTask, session_client: SessionClient, workspace: WorkspaceHandle
) -> None:
    """T-P4-04-11
    Human, Hybrid and a pending label: 422 `not_ai`, nothing queued. An AI task queues (who
    and when are kept), a repeated PUT keeps the first queued time, a replay with the same
    Idempotency-Key answers the stored response, and unqueueing clears the flag.
    """
    for label in ("human", "hybrid", None):
        task = await make_task(label=label, estimate_minutes=30 if label else None)
        refused = await session_client.put(_path(task.id), json={"queued": True})
        assert refused.status_code == 422, refused.text
        assert refused.json()["code"] == "not_ai"
        read = await session_client.get(_path(task.id))
        assert read.status_code == 200, read.text
        assert read.json()["queued"] is False

    ai = await make_task(label="ai", acceptance_criteria="The page loads")
    queued = await session_client.put(
        _path(ai.id), json={"queued": True}, headers={"Idempotency-Key": "queue-once"}
    )
    assert queued.status_code == 200, queued.text
    body = queued.json()
    assert body["task_id"] == str(ai.id)
    assert body["queued"] is True
    assert body["queued_at"] is not None
    assert body["queued_by"] == f"user:{workspace.user_id}"
    assert body["may_run_unattended"] is True

    replay = await session_client.put(
        _path(ai.id), json={"queued": True}, headers={"Idempotency-Key": "queue-once"}
    )
    assert replay.status_code == 200
    assert replay.headers.get("Idempotent-Replayed") == "true"
    again = await session_client.put(_path(ai.id), json={"queued": True})
    assert again.status_code == 200, again.text
    assert again.json()["queued_at"] == body["queued_at"]

    unqueued = await session_client.put(_path(ai.id), json={"queued": False})
    assert unqueued.status_code == 200, unqueued.text
    assert unqueued.json()["queued"] is False
    assert unqueued.json()["queued_at"] is None
    assert (await session_client.get(_path(ai.id))).json()["queued"] is False

    # Unqueueing a task that was never queued (any label) is a no-op, not an error.
    human = await make_task(label="human", estimate_minutes=30)
    cleared = await session_client.put(_path(human.id), json={"queued": False})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["queued"] is False


@pytest.mark.req("FR-4.5")
@pytest.mark.wp("P4-04")
async def test_a_done_task_cannot_be_queued(
    make_task: MakeTask, set_status: SetStatus, session_client: SessionClient
) -> None:
    """A done AI task answers 422 `task_done` and stays off the queue (every window would
    only refuse it again); taking it off still works."""
    task = await make_task(label="ai", acceptance_criteria="The page loads", status="today")
    working = await set_status(task, "in_progress", actor="agent")
    done = await set_status(await set_status(working, "in_review", actor="agent"), "done")
    refused = await session_client.put(_path(done.id), json={"queued": True})
    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "task_done"
    assert (await session_client.get(_path(done.id))).json()["queued"] is False
    cleared = await session_client.put(_path(done.id), json={"queued": False})
    assert cleared.status_code == 200, cleared.text
