"""Board columns per project (P0-18, FR-3.2): six defaults made when a project is created
(a tasks subscriber of `project.created`, idempotent), renamed and reordered with one PUT,
and never a set that leaves a status without a column."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from psycopg.rows import dict_row

from tests._pg import OWNER

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tumnis.modules.tasks.tests.conftest import MakeProject

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

DEFAULTS = [
    ("Backlog", "backlog"),
    ("Today", "today"),
    ("In progress", "in_progress"),
    ("Waiting on human", "waiting_on_human"),
    ("In review", "in_review"),
    ("Done", "done"),
]


def _outbox_row(db: DbUrls, name: str) -> dict[str, Any]:
    with psycopg.connect(db.libpq(OWNER), row_factory=dict_row) as conn:
        row = conn.execute("SELECT * FROM outbox WHERE name = %s", (name,)).fetchone()
    assert row is not None
    return row


@pytest.mark.req("FR-3.2")
@pytest.mark.wp("P0-18")
@pytest.mark.xfail(strict=True, reason="spec:P0-18")
async def test_default_columns_and_edits(
    app: FastAPI, session_client: SessionClient, make_project: MakeProject, db: DbUrls
) -> None:
    """T-P0-18-18
    The `tasks.create_default_columns` subscriber of `project.created`, delivered twice,
    leaves the six default columns once (Backlog, Today, In progress, Waiting on human, In
    review, Done). `PUT /v1/projects/{id}/columns` renames and reorders them (ids kept) and
    may add a second column for a status; a set that leaves a status without a column is
    422 `status_without_column` and changes nothing.
    """
    from tumnis.core.events import EventEnvelope, subscribers_for  # noqa: PLC0415
    from tumnis.modules.tasks import events  # noqa: PLC0415

    project = await make_project()
    assert "tasks.create_default_columns" in {
        sub.name for sub in subscribers_for("project.created")
    }
    envelope = EventEnvelope.from_outbox_row(_outbox_row(db, "project.created"))
    await events.create_default_columns(envelope)
    await events.create_default_columns(envelope)  # a redelivery writes nothing

    url = f"/v1/projects/{project.id}/columns"
    listed = await session_client.get(url)
    assert listed.status_code == 200, listed.text
    columns = listed.json()["items"]
    assert [(c["name"], c["status"]) for c in columns] == DEFAULTS

    backlog, today, *rest = columns
    edited = [
        {"id": today["id"], "name": "Today", "status": "today"},
        {"id": backlog["id"], "name": "Someday", "status": "backlog"},
        *({"id": c["id"], "name": c["name"], "status": c["status"]} for c in rest),
        {"name": "Blocked elsewhere", "status": "waiting_on_human"},
    ]
    put = await session_client.put(url, json={"columns": edited})
    assert put.status_code == 200, put.text
    after = (await session_client.get(url)).json()["items"]
    assert [(c["name"], c["status"]) for c in after] == [
        ("Today", "today"),
        ("Someday", "backlog"),
        *DEFAULTS[2:],
        ("Blocked elsewhere", "waiting_on_human"),
    ]
    assert [c["id"] for c in after[:6]] == [
        today["id"],
        backlog["id"],
        *(c["id"] for c in rest),
    ]

    without_done = [
        {"id": c["id"], "name": c["name"], "status": c["status"]}
        for c in after
        if c["status"] != "done"
    ]
    refused = await session_client.put(url, json={"columns": without_done})
    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "status_without_column"
    assert (await session_client.get(url)).json()["items"] == after
