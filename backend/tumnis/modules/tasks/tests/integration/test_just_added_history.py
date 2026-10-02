"""The dashboard's Just added list and a task's History (A1.1, journey J2; coordinator
decision 83).

- `GET /v1/just-added`: the signed-in person's own captures of today (the workspace's day)
  still in Backlog, newest first, at most three. Others' tasks, older ones, planned or
  trashed ones stay out.
- `GET /v1/tasks/{id}/history`: the task's writes from `task_changes`, newest first: which
  fields each changed (`created` for the task itself), who made it and whether that was
  the caller (`by_you`), so the drawer can say a label came from "you".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.tasks.tests.conftest import owner_rows

if TYPE_CHECKING:
    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tumnis.modules.tasks.tests.conftest import MakeTask

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


async def _created(client: SessionClient, body: dict[str, Any]) -> dict[str, Any]:
    response = await client.post("/v1/tasks", json=body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


@pytest.mark.req("FR-3.3")
@pytest.mark.wp("P1-08")
async def test_just_added_is_my_backlog_captures_of_today(
    session_client: SessionClient, make_task: MakeTask, db: DbUrls
) -> None:
    """Five Backlog captures of mine (one of them made two days ago, one trashed), one I
    planned for Today and one an agent added: the list is my three newest of today's that
    are still in Backlog, newest first."""
    project = (await session_client.post("/v1/projects", json={"name": "Captures"})).json()

    async def capture(title: str, **fields: Any) -> dict[str, Any]:
        body = {"project_id": project["id"], "title": title, "label": "human", **fields}
        return await _created(session_client, body)

    old = await capture("two days ago")
    owner_rows(
        db,
        "UPDATE tasks SET created_at = now() - interval '2 days' WHERE id = %s RETURNING id",
        (old["id"],),
    )
    for title in ("first", "second", "third"):
        await capture(title)
    await capture("planned", status="today")
    trashed = await capture("trashed")
    owner_rows(
        db, "UPDATE tasks SET deleted_at = now() WHERE id = %s RETURNING id", (trashed["id"],)
    )
    await make_task(actor="agent", project_id=project["id"], title="an agent's")
    newest = await capture("fourth")

    response = await session_client.get("/v1/just-added")

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["title"] for item in items] == ["fourth", "third", "second"]
    assert items[0]["id"] == newest["id"]
    assert all(item["status"] == "backlog" for item in items)


@pytest.mark.req("FR-4.2")
@pytest.mark.wp("P1-08")
async def test_history_names_each_write_and_whether_it_was_yours(
    session_client: SessionClient, make_task: MakeTask
) -> None:
    """An agent adds a task; I rename it, then label it Human: the history is my label,
    my title, then the agent's creation, newest first, and only mine say `by_you`."""
    task = await make_task(actor="agent", title="Draft the brief")
    path = f"/v1/tasks/{task.id}"
    renamed = await session_client.patch(path, json={"title": "Draft the brief v2", "version": 1})
    assert renamed.status_code == 200, renamed.text
    labelled = await session_client.patch(
        path, json={"label": "human", "version": renamed.json()["version"]}
    )
    assert labelled.status_code == 200, labelled.text

    response = await session_client.get(f"{path}/history")

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [(item["fields"], item["by_you"]) for item in items] == [
        (["label"], True),
        (["title"], True),
        (["created"], False),
    ]
    assert items[0]["change_id"] == labelled.json()["change_id"]
    assert items[2]["actor"].startswith("api_key:")
