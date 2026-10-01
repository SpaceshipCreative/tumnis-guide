"""A project's Activity view (P2-17, FR-2.6): `GET /v1/projects/{id}/activity`, the
project's agent runs, their results and its audit trail in one list, newest first, paged
by cursor. Audit rows belong to a project through `project_id` in their details (written
from P2-17 on; older rows without it are not shown)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

import pytest

from tests._mcp import http_for
from tumnis.modules.agents.tests.integration._human import started
from tumnis.modules.agents.tests.integration._runs import owner_rows, relay, run_world

if TYPE_CHECKING:
    from dbos import DBOS
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]

SUMMARY = "Fixed the footer link"


async def _pages(http: SessionClient, url: str, limit: int) -> list[dict[str, Any]]:
    """Every item, page by page; each page holds at most `limit`."""
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(50):
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        page = await http.get(url, params=params)
        assert page.status_code == 200, page.text
        body = page.json()
        assert len(body["items"]) <= limit
        items.extend(body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            return items
    raise AssertionError("the activity never ended")


@pytest.mark.req("FR-2.6")
@pytest.mark.wp("P2-17")
@pytest.mark.xfail(strict=True, reason="spec:P2-17")
async def test_activity_lists_runs_results_and_audit(  # noqa: PLR0917
    app: FastAPI,
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-17-02
    The Acme site project's activity holds its two runs, the result one of them posted and
    the audit rows written for it, newest first, in cursor pages of the asked size with no
    row twice. Beta app's run and audit row, and an audit row without a project, are not
    in it. A malformed cursor is 400 `invalid_cursor`."""
    from tumnis.core import audit  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    world = await run_world(fake_runner, workspace, clock, others=(("Beta app", "beta-app"),))
    acme, beta = world.project_id, world.projects["Beta app"]
    fixed = await world.ai_task("Fix footer link")
    pending = await world.ai_task("Update the pricing page")
    theirs = await world.ai_task("Beta onboarding copy", project_id=beta)
    async with relay(db):
        done_run = await started(world, db, fixed.id)
        async with http_for(app, world.token(done_run)) as http:
            posted = await http.post(
                f"/v1/runs/{done_run}/result",
                json={"run_id": str(done_run), "outcome": "done", "summary": SUMMARY},
                headers={"Idempotency-Key": f"activity-{uuid.uuid4()}"},
            )
        assert posted.status_code == 200, posted.text
        open_run = await world.request(pending.id)
        beta_run = await world.request(theirs.id)

    async with tenant_session(world.ctx) as s:
        for project_id, action in ((acme, "approval.granted"), (beta, "approval.denied")):
            scoped: dict[str, Any] = {"project_id": project_id}  # P2-17's audit keyword
            await audit.record(
                s,
                action,
                target=("approval", uuid.uuid4()),
                reason="Checked the diff",
                details={"action_class": "merge_main"},
                occurred_at=clock.now(),
                **scoped,
            )
        await audit.record(s, "agent.gated_action", details={}, occurred_at=clock.now())
    [(acme_audit,)] = owner_rows(db, "SELECT id FROM audit_log WHERE action = 'approval.granted'")

    url = f"/v1/projects/{acme}/activity"
    items = await _pages(session_client, url, limit=2)
    ids = [item["id"] for item in items]
    assert len(ids) == len(set(ids)), "a row came twice"
    stamps = [datetime.fromisoformat(item["at"]) for item in items]
    assert stamps == sorted(stamps, reverse=True), "not newest first"

    runs = {item["run_id"] for item in items if item["kind"] == "run"}
    assert runs == {str(done_run), str(open_run)}
    assert str(beta_run) not in {item["run_id"] for item in items}
    [result] = [item for item in items if item["kind"] == "result"]
    assert (result["run_id"], result["task_id"]) == (str(done_run), str(fixed.id))
    assert result["summary"] == SUMMARY
    audits = [item for item in items if item["kind"] == "audit"]
    assert [(a["id"], a["action"]) for a in audits] == [(str(acme_audit), "approval.granted")]

    bad = await session_client.get(url, params={"cursor": "not-a-cursor"})
    assert bad.status_code == 400, bad.text
    assert bad.json()["code"] == "invalid_cursor"
