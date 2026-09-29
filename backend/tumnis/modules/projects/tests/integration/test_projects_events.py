"""`project.created` fires once per project (P0-17, FR-2.1): the outbox row commits with
the project, and an idempotent retry replays the answer without a second event."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _owner(db: DbUrls, query: str) -> list[tuple[Any, ...]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        return conn.execute(query.encode()).fetchall()


@pytest.mark.req("FR-2.1")
@pytest.mark.wp("P0-17")
async def test_project_created_fires_once(
    app: FastAPI,
    session_client: SessionClient,
    workspace: WorkspaceHandle,
    db: DbUrls,
    repo_root: Path,
) -> None:
    """T-P0-17-15
    `POST /v1/projects` sent twice with the same `Idempotency-Key` and body: the second
    answer is the replay, one project row and one `project.created` outbox row exist, and
    the payload validates against `schemas/events/v1/project.created.json`.
    """
    from jsonschema import Draft202012Validator  # type: ignore[import-untyped]  # noqa: PLC0415

    body = {"name": "Acme site", "client": "Acme", "brief_md": "# Goal"}
    headers = {"Idempotency-Key": "create-acme-site"}
    first = await session_client.post("/v1/projects", json=body, headers=headers)
    second = await session_client.post("/v1/projects", json=body, headers=headers)
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert second.headers.get("Idempotent-Replayed") == "true"
    assert second.json()["id"] == first.json()["id"]

    assert _owner(db, "SELECT count(*) FROM projects") == [(1,)]
    events = _owner(db, "SELECT payload FROM outbox WHERE name = 'project.created'")
    assert len(events) == 1
    payload = events[0][0]
    assert payload["project_id"] == first.json()["id"]
    assert payload["brief_md"] == "# Goal"
    schema = json.loads((repo_root / "schemas/events/v1/project.created.json").read_text())
    Draft202012Validator(schema).validate(payload)
