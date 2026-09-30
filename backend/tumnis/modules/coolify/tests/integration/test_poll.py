"""The Coolify poll and the status read (P2-14, FR-12.2): the scheduled tick reads every
application a live project links, stores its last deployment and previews, keeps the last
good status (out of date) when a read fails, and the status route answers per project,
limited to a key's projects."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from dbos import DBOS
    from fastapi import FastAPI

    from tests._pg import DbUrls
    from tests.fixtures import KeyClientFactory, WorkspaceHandle
    from tumnis.modules.coolify.adapters.fake import FakeCoolifyStatus
    from tumnis.modules.coolify.tests.integration.conftest import LinkProject

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

T0 = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
PORTAL = "dune0portal0example00004"
API = "brio0api0example00000002"
SITE = "acme0site0example0000001"
WEB = "cove0web0example00000003"


async def _status(workspace: WorkspaceHandle) -> list[dict[str, Any]]:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.coolify import api  # noqa: PLC0415

    async with tenant_session(workspace.ctx) as s:
        found = await api.deploy_status(s)
    return [entry.model_dump(mode="json") for entry in found]


@pytest.mark.req("FR-12.2")
@pytest.mark.wp("P2-14")
async def test_tick_records_last_deploy_and_previews_of_linked_apps(
    app_db: DbUrls,
    workspace: WorkspaceHandle,
    coolify: FakeCoolifyStatus,
    link_project: LinkProject,
    dbos: type[DBOS],
) -> None:
    """Projects A (portal and api) and B (site) are polled; the archived project's app is
    not. Without GitHub every preview of the recent deployments is listed."""
    from tumnis.modules.coolify.workflows import coolify_poll_tick, schedules  # noqa: PLC0415

    (tick,) = schedules()
    assert (tick["schedule_name"], tick["schedule"]) == ("coolify-poll", "*/5 * * * *")

    a = await link_project("Dune", PORTAL, API)
    b = await link_project("Acme", SITE)
    await link_project("Cove", WEB, archived=True)
    await coolify_poll_tick(T0, None)

    assert sorted(uuid for _, uuid in coolify.calls) == sorted(
        [PORTAL, PORTAL, API, API, SITE, SITE]
    )
    status = await _status(workspace)
    assert [entry["project_id"] for entry in status] == [str(a.id), str(b.id)]
    portal, api_app = status[0]["apps"]
    assert portal == {
        "app_uuid": PORTAL,
        "name": "dune-portal",
        "last": {
            "status": "finished",
            "commit": "f0e1d2c3b4a5968778695a4b3c2d1e0f9a8b7c6d",
            "created_at": "2026-03-09T09:00:00Z",
            "finished_at": "2026-03-09T09:03:30Z",
        },
        "previews": [
            {
                "pull_request_id": 11,
                "url": "https://11.portal.example.org",
                "status": "finished",
                "commit": "99887766554433221100ffeeddccbbaa99887766",
                "finished_at": "2026-03-08T12:03:00Z",
            },
            {
                "pull_request_id": 14,
                "url": "https://14.portal.example.org",
                "status": "finished",
                "commit": "aa11bb22cc33dd44ee55ff6677889900aabbccdd",
                "finished_at": "2026-03-09T11:23:10Z",
            },
        ],
        "checked_at": "2026-03-09T12:00:00Z",
        "error": None,
    }
    assert (api_app["name"], api_app["last"]["status"]) == ("brio-api", "failed")
    (site,) = status[1]["apps"]
    assert (site["last"]["status"], site["last"]["commit"][:7]) == ("finished", "3f9c2a1")


@pytest.mark.req("FR-12.2")
@pytest.mark.wp("P2-14")
async def test_failed_poll_keeps_the_last_status_out_of_date(
    app_db: DbUrls,
    workspace: WorkspaceHandle,
    coolify: FakeCoolifyStatus,
    link_project: LinkProject,
    dbos: type[DBOS],
) -> None:
    """Coolify down for the portal: its last status stays with `error` unavailable and the
    first check time; an app Coolify does not know is rejected with nothing to show; the
    next good poll clears the error."""
    from tumnis.modules.coolify.workflows import poll_workspace  # noqa: PLC0415

    await link_project("Dune", PORTAL, "gone0example000000000000")
    assert await poll_workspace(str(workspace.id), T0) == 1

    coolify.unavailable.add(PORTAL)
    assert await poll_workspace(str(workspace.id), T0 + timedelta(minutes=5)) == 0
    portal, gone = (await _status(workspace))[0]["apps"]
    assert portal["error"] == "unavailable"
    assert portal["checked_at"] == "2026-03-09T12:00:00Z"
    assert portal["last"]["status"] == "finished"
    assert gone == {
        "app_uuid": "gone0example000000000000",
        "name": None,
        "last": None,
        "previews": [],
        "checked_at": None,
        "error": "rejected",
    }

    coolify.unavailable.clear()
    assert await poll_workspace(str(workspace.id), T0 + timedelta(minutes=10)) == 1
    portal = (await _status(workspace))[0]["apps"][0]
    assert (portal["error"], portal["checked_at"]) == (None, "2026-03-09T12:10:00Z")


@pytest.mark.req("FR-12.2")
@pytest.mark.wp("P2-14")
async def test_real_mode_skips_a_workspace_without_settings(
    app_db: DbUrls,
    workspace: WorkspaceHandle,
    link_project: LinkProject,
    dbos: type[DBOS],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With real adapters and no Coolify URL and token, nothing is called or stored."""
    from tumnis.modules.coolify import workflows  # noqa: PLC0415

    monkeypatch.setenv("TUMNIS_ADAPTERS", "real")
    await link_project("Dune", PORTAL)
    assert await workflows.poll_workspace(str(workspace.id), T0) == 0
    assert (await _status(workspace))[0]["apps"][0]["checked_at"] is None


@pytest.mark.req("FR-12.2", "FR-14.10")
@pytest.mark.wp("P2-14")
async def test_status_route_answers_per_project_within_a_keys_projects(  # noqa: PLR0917
    app: FastAPI,
    workspace: WorkspaceHandle,
    key_client: KeyClientFactory,
    coolify: FakeCoolifyStatus,
    link_project: LinkProject,
    dbos: type[DBOS],
) -> None:
    """`GET /v1/coolify/status` lists both projects to a read key, one with `project_id`;
    a key limited to B sees only B and gets 404 for A (R-28)."""
    from tumnis.modules.coolify.workflows import poll_workspace  # noqa: PLC0415

    a = await link_project("Dune", PORTAL)
    b = await link_project("Acme", SITE)
    await link_project("Plain")  # no app: not listed
    await poll_workspace(str(workspace.id), T0)

    reader = await key_client(frozenset({"tasks:read"}))
    async with reader:
        every = await reader.get("/v1/coolify/status")
        one = await reader.get("/v1/coolify/status", params={"project_id": str(a.id)})
    assert every.status_code == 200, every.text
    assert [entry["project_id"] for entry in every.json()] == [str(a.id), str(b.id)]
    assert [entry["project_id"] for entry in one.json()] == [str(a.id)]
    assert one.json()[0]["apps"][0]["name"] == "dune-portal"

    limited = await key_client(frozenset({"tasks:read"}), projects={b.id})
    async with limited:
        mine = await limited.get("/v1/coolify/status")
        other = await limited.get("/v1/coolify/status", params={"project_id": str(a.id)})
    assert [entry["project_id"] for entry in mine.json()] == [str(b.id)]
    assert other.status_code == 404
    assert other.json()["code"] == "not_found"
