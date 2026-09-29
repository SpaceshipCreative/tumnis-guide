"""Task writes emit their events in the same transaction (P0-18, FR-3.1, R-06):
`task.created`, `task.status_changed` and `task.updated`, one outbox row each, valid against
the published schemas."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.tasks.tests.conftest import outbox, owner_rows

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.modules.tasks.tests.conftest import MakeProject

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _validate(repo_root: Path, name: str, payload: dict[str, Any]) -> None:
    from jsonschema import Draft202012Validator  # type: ignore[import-untyped]  # noqa: PLC0415

    schema = json.loads((repo_root / f"schemas/events/v1/{name}.json").read_text())
    Draft202012Validator(schema).validate(payload)


@pytest.mark.req("FR-3.1")
@pytest.mark.wp("P0-18")
@pytest.mark.xfail(strict=True, reason="spec:P0-18")
async def test_task_created_and_status_changed_fire(  # noqa: PLR0917
    app: FastAPI,
    session_client: SessionClient,
    make_project: MakeProject,
    workspace: WorkspaceHandle,
    db: DbUrls,
    repo_root: Path,
) -> None:
    """T-P0-18-13
    `POST /v1/tasks` writes one `task.created` row (task, project, label, source, taint and
    the search `doc`); `POST /v1/tasks/{id}/status {to: today}` writes one
    `task.status_changed` row (`from` backlog, `to` today, the acting user); both validate
    against `schemas/events/v1/`.
    """
    project = await make_project()
    created = await session_client.post(
        "/v1/tasks",
        json={
            "project_id": str(project.id),
            "title": "Collect logo references",
            "label": "human",
            "estimate_minutes": 30,
            "first_action": "Open the moodboard",
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()
    [payload] = outbox(db, "task.created")
    assert payload["task_id"] == task["id"]
    assert payload["project_id"] == str(project.id)
    assert (payload["label"], payload["source"], payload["tainted"]) == ("human", "user", False)
    assert payload["doc"]["title"] == "Collect logo references"
    assert "Open the moodboard" in payload["doc"]["body"]
    assert payload["doc"]["deleted"] is False
    _validate(repo_root, "task.created", payload)

    moved = await session_client.post(
        f"/v1/tasks/{task['id']}/status", json={"to": "today", "version": task["version"]}
    )
    assert moved.status_code == 200, moved.text
    [changed] = outbox(db, "task.status_changed")
    assert changed == {
        "schema_version": 1,
        "task_id": task["id"],
        "from": "backlog",
        "to": "today",
        "actor": f"user:{workspace.user_id}",
    }
    _validate(repo_root, "task.status_changed", changed)


@pytest.mark.req("FR-3.1")
@pytest.mark.wp("P0-18")
@pytest.mark.xfail(strict=True, reason="spec:P0-18")
async def test_task_updated_payload_lists_changed_fields(
    app: FastAPI,
    session_client: SessionClient,
    make_project: MakeProject,
    db: DbUrls,
    repo_root: Path,
) -> None:
    """T-P0-18-22
    `PATCH /v1/tasks/{id} {title, due_on, version}` emits one `task.updated` whose payload
    is `{task_id, changed_fields: ["due_on", "title"], doc}` (sorted) and validates against
    its schema. A task created without a label stores `label` and `label_source` NULL;
    setting the label through the API stores `label_source = 'user'`.
    """
    project = await make_project()
    created = await session_client.post(
        "/v1/tasks", json={"project_id": str(project.id), "title": "Tidy the drive"}
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert (task["label"], task["label_source"]) == (None, None)
    assert owner_rows(db, "SELECT label, label_source FROM tasks WHERE id = %s", (task["id"],)) == [
        (None, None)
    ]

    patched = await session_client.patch(
        f"/v1/tasks/{task['id']}",
        json={"title": "Tidy the shared drive", "due_on": "2026-03-20", "version": task["version"]},
    )
    assert patched.status_code == 200, patched.text
    [payload] = outbox(db, "task.updated")
    assert payload["task_id"] == task["id"]
    assert payload["changed_fields"] == ["due_on", "title"]
    assert payload["doc"]["title"] == "Tidy the shared drive"
    _validate(repo_root, "task.updated", payload)

    labelled = await session_client.patch(
        f"/v1/tasks/{task['id']}", json={"label": "human", "version": patched.json()["version"]}
    )
    assert labelled.status_code == 200, labelled.text
    assert labelled.json()["label_source"] == "user"
    assert owner_rows(
        db, "SELECT label::text, label_source FROM tasks WHERE id = %s", (task["id"],)
    ) == [("human", "user")]
