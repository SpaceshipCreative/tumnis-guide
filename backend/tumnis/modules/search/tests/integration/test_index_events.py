"""Task changes reach the search index only through events (P0-20, FR-3.9): search calls
no other module, and an event delivered late never overwrites a newer row."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.search.tests.integration import _search

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

# The search subscriber of `task.updated`; its name is part of every delivery's workflow
# ID, so it is spelled out here.
TASK_UPDATED_SUBSCRIBER = "search.index_task_updated"


async def _hits(client: SessionClient, q: str) -> list[dict[str, Any]]:
    answer = await client.get("/v1/search", params={"q": q})
    assert answer.status_code == 200, answer.text
    items: list[dict[str, Any]] = answer.json()["items"]
    return items


@pytest.mark.req("FR-3.9")
@pytest.mark.wp("P0-20")
async def test_task_changes_reach_index_through_events(
    db: DbUrls,
    dbos: type[DBOS],
    session_client: SessionClient,
    workspace: WorkspaceHandle,
    clock: FixedClock,
) -> None:
    """T-P0-20-03
    Create "Send invoice to Acme"; after the relay drains, `GET /v1/search?q=invoice`
    returns it. Rename it to "Send quote"; `invoice` no longer matches and `quote` does.
    Trash it; nothing matches.
    """
    project = await session_client.post("/v1/projects", json={"name": "Acme"})
    assert project.status_code == 201, project.text
    created = await session_client.post(
        "/v1/tasks",
        json={"project_id": project.json()["id"], "title": "Send invoice to Acme"},
    )
    assert created.status_code == 201, created.text
    task = created.json()
    await _search.drain(db)

    [hit] = await _hits(session_client, "invoice")
    assert (hit["entity_type"], hit["entity_id"]) == ("task", task["id"])
    assert hit["project_id"] == project.json()["id"]
    assert hit["title"] == "Send invoice to Acme"

    clock.advance(timedelta(minutes=1))
    current = await session_client.get(f"/v1/tasks/{task['id']}")
    renamed = await session_client.patch(
        f"/v1/tasks/{task['id']}",
        json={"title": "Send quote", "version": current.json()["version"]},
    )
    assert renamed.status_code == 200, renamed.text
    await _search.drain(db)
    assert await _hits(session_client, "invoice") == []
    assert [h["entity_id"] for h in await _hits(session_client, "quote")] == [task["id"]]

    clock.advance(timedelta(minutes=1))
    await _search.trash_task(workspace.ctx, uuid.UUID(task["id"]), clock.now())
    await _search.drain(db)
    assert await _hits(session_client, "quote") == []
    assert await _hits(session_client, "send") == []


@pytest.mark.req("FR-3.9")
@pytest.mark.wp("P0-20")
async def test_out_of_order_event_does_not_overwrite_newer(
    search_db: DbUrls, workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """T-P0-20-04
    Deliver `task.created` (t0), then `task.updated` with `doc.updated_at = t2`, then a
    stale `task.updated` with t1 < t2 (and a second delivery of the t2 one): the row keeps
    the t2 title and `source_updated_at = t2`.
    """
    import tumnis.modules.search.events  # noqa: F401, PLC0415  # registers the subscribers
    from tests.fixtures import make_envelope  # noqa: PLC0415
    from tumnis.core.events import get_subscriber  # noqa: PLC0415

    task_id, project_id = uuid.uuid4(), uuid.uuid4()
    t0 = clock.now()
    t1, t2 = t0 + timedelta(minutes=1), t0 + timedelta(minutes=2)

    def doc(title: str, at: Any) -> dict[str, Any]:
        return {
            "title": title,
            "body": "",
            "deleted": False,
            "project_id": str(project_id),
            "updated_at": at.isoformat(),
        }

    created = make_envelope(
        "task.created",
        {
            "schema_version": 1,
            "task_id": str(task_id),
            "project_id": str(project_id),
            "label": None,
            "source": "user",
            "tainted": False,
            "doc": doc("First title", t0),
        },
        workspace,
        occurred_at=t0,
    )

    def updated(title: str, at: Any) -> Any:
        payload = {
            "schema_version": 1,
            "task_id": str(task_id),
            "changed_fields": ["title"],
            "doc": doc(title, at),
        }
        return make_envelope("task.updated", payload, workspace, occurred_at=at)

    newer, stale = updated("Newer title", t2), updated("Older title", t1)
    await get_subscriber("search.index_task_created").handler(created)
    handler = get_subscriber(TASK_UPDATED_SUBSCRIBER).handler
    await handler(newer)
    await handler(stale)
    await handler(newer)

    row = _search.index_row(search_db, task_id)
    assert row is not None
    assert row["title"] == "Newer title"
    assert row["source_updated_at"] == t2
    assert row["project_id"] == project_id
    assert row["deleted_at"] is None
