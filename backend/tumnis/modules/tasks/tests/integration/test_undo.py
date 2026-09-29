"""Undo through the server-side change log (P0-24, R-09, UX 9): every task write records
the undoable fields it changed in `task_changes` and answers the row's id as `change_id`;
`POST /v1/tasks/{id}/undo {change_id, version}` puts the `before` back for a signed-in
human, once, and only while nothing changed the task since. History fields (rollover
count, start and completion history) are never part of a change."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.tasks.tests.conftest import owner_rows

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import KeyClientFactory
    from tumnis.modules.tasks.tests.conftest import MakeTask, SetStatus

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

HISTORY = {"rollover_count", "started_at", "completed_at", "actual_minutes"}


def _change(db: DbUrls, change_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = owner_rows(
        db, "SELECT before, after FROM task_changes WHERE change_id = %s", (change_id,)
    )
    assert len(rows) == 1, rows
    before, after = rows[0]
    return before, after


@pytest.mark.req("UX 9")
@pytest.mark.wp("P0-24")
async def test_undo_restores_and_guards(  # noqa: PLR0917  # the fixtures it needs
    app: FastAPI,
    db: DbUrls,
    session_client: SessionClient,
    key_client: KeyClientFactory,
    make_task: MakeTask,
    set_status: SetStatus,
) -> None:
    """T-P0-24-12
    A Human task in progress with `rollover_count=2` is marked done through REST: the
    answer carries `change_id` c1, and the change row holds `before.status = in_progress`,
    `after.status = done` and no history field. Undo `{c1, version}` restores In progress,
    clears `completed_at` and answers a new change id; repeating it is 409
    `already_undone`. After a title edit and a newer priority edit, undoing the title edit
    is 409 `stale_version`. An API key with `tasks:write` gets 403. Trash then undo brings
    the task back. The rollover count stays 2 throughout.
    """
    task = await make_task(label="human", estimate_minutes=30)
    owner_rows(db, "UPDATE tasks SET rollover_count = 2 WHERE id = %s RETURNING id", (task.id,))
    got = await session_client.get(f"/v1/tasks/{task.id}")
    assert got.status_code == 200, got.text
    started = await session_client.post(
        f"/v1/tasks/{task.id}/status", json={"to": "in_progress", "version": got.json()["version"]}
    )
    assert started.status_code == 200, started.text

    done = await session_client.post(
        f"/v1/tasks/{task.id}/status",
        json={"to": "done", "version": started.json()["version"]},
    )
    assert done.status_code == 200, done.text
    c1, v5 = done.json()["change_id"], done.json()["version"]
    assert c1 is not None
    before, after = _change(db, c1)
    assert before["status"] == "in_progress"
    assert after["status"] == "done"
    assert not (set(before) | set(after)) & HISTORY

    undone = await session_client.post(
        f"/v1/tasks/{task.id}/undo", json={"change_id": c1, "version": v5}
    )
    assert undone.status_code == 200, undone.text
    restored = undone.json()
    assert restored["status"] == "in_progress"
    assert restored["completed_at"] is None
    assert restored["rollover_count"] == 2
    assert restored["change_id"] not in {None, c1}

    again = await session_client.post(
        f"/v1/tasks/{task.id}/undo", json={"change_id": c1, "version": restored["version"]}
    )
    assert again.status_code == 409, again.text
    assert again.json()["code"] == "already_undone"

    renamed = await session_client.patch(
        f"/v1/tasks/{task.id}", json={"title": "Renamed", "version": restored["version"]}
    )
    assert renamed.status_code == 200, renamed.text
    c_title, v_title = renamed.json()["change_id"], renamed.json()["version"]
    newer = await session_client.patch(
        f"/v1/tasks/{task.id}", json={"priority": "high", "version": v_title}
    )
    assert newer.status_code == 200, newer.text
    stale = await session_client.post(
        f"/v1/tasks/{task.id}/undo", json={"change_id": c_title, "version": v_title}
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "stale_version"
    assert stale.json()["current"]["title"] == "Renamed"

    agent = await key_client(frozenset({"tasks:read", "tasks:write"}))
    refused = await agent.post(
        f"/v1/tasks/{task.id}/undo",
        json={"change_id": newer.json()["change_id"], "version": newer.json()["version"]},
    )
    assert refused.status_code == 403, refused.text

    trashed = await session_client.request(
        "DELETE", f"/v1/tasks/{task.id}", json={"version": newer.json()["version"]}
    )
    assert trashed.status_code == 200, trashed.text
    assert (await session_client.get(f"/v1/tasks/{task.id}")).status_code == 404
    back = await session_client.post(
        f"/v1/tasks/{task.id}/undo",
        json={"change_id": trashed.json()["change_id"], "version": trashed.json()["version"]},
    )
    assert back.status_code == 200, back.text
    final = await session_client.get(f"/v1/tasks/{task.id}")
    assert final.status_code == 200, final.text
    assert final.json()["rollover_count"] == 2
    assert final.json()["title"] == "Renamed"
