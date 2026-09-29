"""Optimistic concurrency: a write names the version it read; a stale one is 409 with the
current row (P0-10, REL-2)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx

    from tests._pg import DbUrls
    from tumnis.core.tests.integration._demo import Demo

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("REL-2")
@pytest.mark.wp("P0-10")
@pytest.mark.xfail(strict=True, reason="spec:P0-10")
async def test_stale_version_returns_409_with_current_row(
    db: DbUrls, demo_app: Demo, demo_client: httpx.AsyncClient
) -> None:
    """T-P0-10-08
    PATCH with version 1 after another write: 409 `stale_version`, `current.version == 2`,
    the current fields present; the stale write changed nothing.
    """
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core.tests.integration._demo import principal_header  # noqa: PLC0415

    who = principal_header(make_workspace(db), uuid.uuid4())

    def key() -> dict[str, str]:
        return {**who, "Idempotency-Key": f"versioning-{uuid.uuid4()}"}

    created = await demo_client.post(
        "/v1/demo-items", json={"title": "first", "due_on": "2026-03-10"}, headers=key()
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["version"] == 1
    path = f"/v1/demo-items/{item['id']}"

    moved = await demo_client.patch(path, json={"title": "second", "version": 1}, headers=key())
    assert moved.status_code == 200, moved.text
    assert moved.json()["version"] == 2

    stale = await demo_client.patch(path, json={"title": "third", "version": 1}, headers=key())
    assert stale.status_code == 409, stale.text
    assert stale.headers["content-type"].startswith("application/problem+json")
    problem = stale.json()
    assert problem["code"] == "stale_version"
    current = problem["current"]
    assert current["version"] == 2
    assert current["id"] == item["id"]
    assert current["title"] == "second"
    assert current["due_on"] == "2026-03-10"

    listed = await demo_client.get("/v1/demo-items", headers=who)
    assert [row["title"] for row in listed.json()["items"]] == ["second"]
