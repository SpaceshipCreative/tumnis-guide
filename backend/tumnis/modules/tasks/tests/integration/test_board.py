"""The board lays subtasks out on read (P0-18, FR-3.4, FR-3.8): changing the project's card
threshold turns a card into a checklist item on its parent and back, and never touches the
subtask's status, version, comments or links."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.tasks.tests.conftest import owner_rows

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.modules.tasks.tests.conftest import (
        Actors,
        MakeProject,
        MakeSubtask,
        MakeTask,
        SetStatus,
    )

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _cards(board: dict[str, Any], status: str) -> list[str]:
    """Card task ids in the columns mapped to `status`."""
    return [
        card["task"]["id"]
        for column in board["columns"]
        if column["status"] == status
        for card in column["cards"]
    ]


def _checklist(board: dict[str, Any], parent_id: str) -> list[str]:
    for column in board["columns"]:
        for card in column["cards"]:
            if card["task"]["id"] == parent_id:
                return [item["id"] for item in card["checklist"]]
    raise AssertionError(f"no card for {parent_id}")


def _all_cards(board: dict[str, Any]) -> list[str]:
    return [card["task"]["id"] for column in board["columns"] for card in column["cards"]]


@pytest.mark.req("FR-3.8", "FR-3.4")
@pytest.mark.wp("P0-18")
async def test_threshold_change_relays_out_without_losing_data(  # noqa: PLR0917
    app: FastAPI,
    session_client: SessionClient,
    make_project: MakeProject,
    make_task: MakeTask,
    make_subtask: MakeSubtask,
    set_status: SetStatus,
    workspace: WorkspaceHandle,
    actors: Actors,
    db: DbUrls,
) -> None:
    """T-P0-18-11
    Given a Hybrid parent with a 40-minute Human subtask (a card in In progress, one
    comment, one context item) and threshold 30: `PATCH /v1/projects/{id}
    {subtask_threshold_min: 45}` makes `GET /v1/projects/{id}/board` show the subtask as a
    checklist item on the parent; its status, version, comment and context item are
    unchanged. Set back to 30: a card in In progress again.
    """
    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.modules.integrations import api as integrations  # noqa: PLC0415

    project = await make_project(name="Board project")
    parent = await make_task(project_id=project.id, label="hybrid", estimate_minutes=90)
    sub = await make_subtask(parent, label="human", estimate_minutes=40)
    sub = await set_status(sub, "in_progress")
    commented = await session_client.post(
        f"/v1/tasks/{sub.id}/comments", json={"body_md": "Started on the clear-space rules"}
    )
    assert commented.status_code == 201, commented.text
    item = await integrations.link_context(
        WorkspaceContext(workspace.id, actors.human),
        owner_type="task",
        owner_id=sub.id,
        target_type="url",
        target_url="https://example.com/rules",
        added_by=actors.human,
    )
    linked = await session_client.post(
        f"/v1/tasks/{sub.id}/context-items", json={"context_item_id": str(item.id)}
    )
    assert linked.status_code == 201, linked.text
    before = (await session_client.get(f"/v1/tasks/{sub.id}")).json()

    async def board() -> dict[str, Any]:
        answer = await session_client.get(f"/v1/projects/{project.id}/board")
        assert answer.status_code == 200, answer.text
        body: dict[str, Any] = answer.json()
        return body

    async def threshold(minutes: int, version: int) -> int:
        answer = await session_client.patch(
            f"/v1/projects/{project.id}",
            json={"subtask_threshold_min": minutes, "version": version},
        )
        assert answer.status_code == 200, answer.text
        new_version: int = answer.json()["version"]
        return new_version

    first = await board()
    assert first["threshold_min"] == 30
    assert str(sub.id) in _cards(first, "in_progress")
    assert str(parent.id) in _cards(first, "backlog")
    assert str(sub.id) not in _checklist(first, str(parent.id))

    version = await threshold(45, project.version)
    nested = await board()
    assert nested["threshold_min"] == 45
    assert str(sub.id) not in _all_cards(nested)
    assert _checklist(nested, str(parent.id)) == [str(sub.id)]
    [checklist_item] = [
        entry
        for column in nested["columns"]
        for card in column["cards"]
        for entry in card["checklist"]
    ]
    assert (checklist_item["status"], checklist_item["version"]) == ("in_progress", sub.version)

    after = (await session_client.get(f"/v1/tasks/{sub.id}")).json()
    assert after == before
    assert owner_rows(db, "SELECT count(*) FROM task_comments WHERE task_id = %s", (sub.id,)) == [
        (1,)
    ]
    assert owner_rows(
        db, "SELECT context_item_id FROM task_context_items WHERE task_id = %s", (sub.id,)
    ) == [(item.id,)]

    await threshold(30, version)
    again = await board()
    assert str(sub.id) in _cards(again, "in_progress")
    assert _checklist(again, str(parent.id)) == []
