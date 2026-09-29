"""The dashboard's Today query (P0-23, FR-1.2): `GET /v1/tasks?status=today&order=today&
limit=5` returns the first five Today tasks in `tasks.rules.today_order` (priority, then due
date, then created time) and the page carries `total`, so the panel can say "+2 more".

The tasks table and the list route arrive with P0-18; this test only speaks REST, so it
collects before them and turns green once P0-18's routes and P0-23's `order=today` and
`total` are in."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from tests._auth import SessionClient

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


async def _post(client: SessionClient, path: str, body: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(path, json=body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


@pytest.mark.req("FR-1.2")
@pytest.mark.wp("P0-23")
@pytest.mark.xfail(strict=True, reason="spec:P0-23")
async def test_today_order_limit_and_total(session_client: SessionClient) -> None:
    """T-P0-23-09
    Given seven Today tasks (and one Backlog task) with mixed priorities, due dates and
    creation order, `GET /v1/tasks?status=today&order=today&limit=5` returns five items in
    today order (priority urgent > high > normal > low, then earliest due date with undated
    last, then oldest first) and `total == 7`.
    """
    project = await _post(session_client, "/v1/projects", {"name": "Today order"})

    async def task(title: str, **fields: Any) -> None:
        body = {"project_id": project["id"], "title": title, "label": "human", **fields}
        await _post(session_client, "/v1/tasks", body)

    # Created in this order; the expected order below differs from it on every key.
    await task("normal undated", status="today")
    await task("low due soon", status="today", priority="low", due_on="2026-03-10")
    await task("high due later", status="today", priority="high", due_on="2026-03-20")
    await task("normal due soon", status="today", due_on="2026-03-10")
    await task("urgent undated", status="today", priority="urgent")
    await task("high due soon", status="today", priority="high", due_on="2026-03-11")
    await task("normal undated, newer", status="today")
    await task("backlog urgent", status="backlog", priority="urgent")

    response = await session_client.get(
        "/v1/tasks", params={"status": "today", "order": "today", "limit": 5}
    )
    assert response.status_code == 200, response.text
    page = response.json()
    assert [item["title"] for item in page["items"]] == [
        "urgent undated",
        "high due soon",
        "high due later",
        "normal due soon",
        "normal undated",
    ]
    assert all(item["status"] == "today" for item in page["items"])
    assert page["total"] == 7
