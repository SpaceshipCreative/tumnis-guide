"""Approvals (P2-05, FR-5.6, SEC-3): `request_approval` long-polls for the human's decision,
answers `pending` with an id when the poll runs out, and a re-send returns the decision.
The server decides what needs approval (the policy rule, then Decisions for an action the
policy does not name); every decision is audited with its reason.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

import pytest

from tests._mcp import http_for
from tumnis.modules.agents.tests.integration._human import (
    approval,
    ask,
    audit_rows,
    decide,
    decisions_down,
    gated_noul,
    human_waits,
    open_items,
    started,
    taint,
    use_decisions,
)
from tumnis.modules.agents.tests.integration._runs import relay, run_world, wait_until

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

MERGE = "Merge fix-footer into main"


@pytest.mark.req("FR-5.6")
@pytest.mark.wp("P2-05")
async def test_request_approval_long_polls_then_pending(  # noqa: PLR0917
    app: FastAPI,
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P2-05-03
    With the long poll set to 2 s, a gated request (`merge_main`) waits about 2 s, then
    answers `pending` with the approval's id and the rule that gated it."""
    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")
    with human_waits(poll_seconds=2):
        async with relay(db):
            run_id = await started(world, db, task.id)
            loop = asyncio.get_running_loop()
            began = loop.time()
            answer = await approval(app, world.token(run_id), run_id, "merge_main", MERGE)
            took = loop.time() - began
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["status"] == "pending"
    assert uuid.UUID(body["id"])
    assert body["rule"] == "gated_by_policy"
    assert 1.8 <= took < 5.0


@pytest.mark.req("FR-5.6")
@pytest.mark.wp("P2-05")
async def test_resend_returns_decision(  # noqa: PLR0917
    app: FastAPI,
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-05-04
    After the human approves with a reason, the re-sent request (with the approval id)
    returns `approved` with that reason at once."""
    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")
    with human_waits(poll_seconds=1):
        async with relay(db):
            run_id = await started(world, db, task.id)
            token = world.token(run_id)
            first = await approval(app, token, run_id, "merge_main", MERGE)
            assert first.json()["status"] == "pending"
            [item] = open_items(db, "approval")
            approved = await decide(session_client, item, "approve", {"reason": "Checked the diff"})
            assert approved.status_code == 200, approved.text

            loop = asyncio.get_running_loop()
            began = loop.time()
            again = await approval(
                app, token, run_id, "merge_main", MERGE, approval_id=first.json()["id"]
            )
            assert loop.time() - began < 1.0
    assert again.status_code == 200, again.text
    assert again.json()["status"] == "approved"
    assert again.json()["reason"] == "Checked the diff"
    assert again.json()["id"] == first.json()["id"]


@pytest.mark.req("FR-5.6")
@pytest.mark.wp("P2-05")
async def test_decision_during_long_poll_returns_immediately(  # noqa: PLR0917
    app: FastAPI,
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-05-05
    While the request long-polls (30 s here), the human denies it: the waiting call
    returns `denied` with the reason within 1 s of the decision."""
    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")
    with human_waits(poll_seconds=30):
        async with relay(db):
            run_id = await started(world, db, task.id)
            waiting = asyncio.create_task(
                approval(app, world.token(run_id), run_id, "merge_main", MERGE)
            )
            assert await wait_until(lambda: len(open_items(db, "approval")) == 1)
            [item] = open_items(db, "approval")
            loop = asyncio.get_running_loop()
            denied = await decide(session_client, item, "deny", {"reason": "Not this week"})
            assert denied.status_code == 200, denied.text
            decided_at = loop.time()
            answer = await asyncio.wait_for(waiting, 10)
            assert loop.time() - decided_at < 1.0
    assert answer.json()["status"] == "denied"
    assert answer.json()["reason"] == "Not this week"


@pytest.mark.req("FR-5.6")
@pytest.mark.wp("P2-05")
async def test_unknown_action_uses_noul_threshold(  # noqa: PLR0917
    app: FastAPI,
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P2-05-10
    For an action the policy does not name (`rotate_dns_record`), the worker asks the
    approval-need decision point. Confident that it is safe: the request is approved
    (`unknown_decided_safe`) with no review item. Below the threshold, and with Decisions
    down: it stays pending (`unknown_below_threshold`, `decisions_unavailable`) with an
    `approval` review item each."""
    world = await run_world(fake_runner, workspace, clock)
    cases = [
        (gated_noul(0.01), "approved", "unknown_decided_safe"),
        (gated_noul(0.3), "pending", "unknown_below_threshold"),
        (decisions_down(), "pending", "decisions_unavailable"),
    ]
    try:
        with human_waits(poll_seconds=1):
            async with relay(db):
                for provider, status, rule in cases:
                    use_decisions(provider)
                    task = await world.ai_task(f"Rotate the DNS record ({rule})")
                    run_id = await started(world, db, task.id)
                    token = world.token(run_id)
                    first = await approval(
                        app, token, run_id, "rotate_dns_record", "Rotate the record", target="dns"
                    )
                    assert first.status_code == 200, first.text
                    approval_id = first.json()["id"]

                    async def settled(
                        token: str = token,
                        run_id: uuid.UUID = run_id,
                        approval_id: str = approval_id,
                        rule: str = rule,
                    ) -> bool:
                        again = await approval(
                            app,
                            token,
                            run_id,
                            "rotate_dns_record",
                            "Rotate the record",
                            approval_id=approval_id,
                        )
                        return bool(again.json()["rule"] == rule)

                    assert await wait_until(settled, timeout=20), rule
                    final = await approval(
                        app,
                        token,
                        run_id,
                        "rotate_dns_record",
                        "Rotate the record",
                        approval_id=approval_id,
                    )
                    assert final.json()["status"] == status, rule
                    items = [
                        i
                        for i in open_items(db, "approval")
                        if i["payload"]["approval_id"] == approval_id
                    ]
                    assert len(items) == (0 if status == "approved" else 1), rule
    finally:
        use_decisions(None)


@pytest.mark.req("SEC-3")
@pytest.mark.wp("P2-05")
async def test_decisions_audited_with_reason(  # noqa: PLR0917
    app: FastAPI,
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-05-11
    Approving and denying write `approval.granted` and `approval.denied` rows with the
    human as actor, the reason and the request's correlation id; a blank reason is 422
    `reason_required` and writes nothing."""
    world = await run_world(fake_runner, workspace, clock)
    with human_waits(poll_seconds=1):
        async with relay(db):
            for action in ("approve", "deny"):
                task = await world.ai_task(f"Merge it ({action})")
                run_id = await started(world, db, task.id)
                await approval(app, world.token(run_id), run_id, "merge_main", MERGE)
                [item] = open_items(db, "approval")

                blank = await decide(session_client, item, action, {"reason": "  "})
                assert blank.status_code == 422, blank.text
                assert blank.json()["code"] == "reason_required"

                done = await decide(
                    session_client,
                    item,
                    action,
                    {"reason": f"Because {action}"},
                    request_id=f"req-{action}-1234",
                )
                assert done.status_code == 200, done.text

    granted = audit_rows(db, "approval.granted")
    denied = audit_rows(db, "approval.denied")
    assert len(granted) == 1
    assert len(denied) == 1
    for row, action in ((granted[0], "approve"), (denied[0], "deny")):
        assert row["actor_type"] == "user"
        assert row["actor_id"] == str(workspace.user_id)
        assert row["reason"] == f"Because {action}"
        assert row["correlation_id"] == f"req-{action}-1234"
        assert row["target_type"] == "approval"


@pytest.mark.req("SEC-3")
@pytest.mark.wp("P2-05")
async def test_gated_request_writes_gated_action_audit(  # noqa: PLR0917
    app: FastAPI,
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P2-05-12
    Each request that needs approval writes one `agent.gated_action` row (the task token
    as actor, the action class and the rule): a gated class on a clean run and an allowed
    class on a tainted run. An allowed class on a clean run writes none; a re-send writes
    nothing more."""
    world = await run_world(fake_runner, workspace, clock)
    clean = await world.ai_task("Fix footer link")
    dirty = await world.ai_task("Update the footer colours")
    await taint(world, dirty.id)
    with human_waits(poll_seconds=0):
        async with relay(db):
            run1 = await started(world, db, clean.id)
            token1 = world.token(run1)
            allowed = await approval(app, token1, run1, "push_feature_branch", "Push fix-footer")
            assert allowed.json()["status"] == "approved"
            merge = await approval(app, token1, run1, "merge_main", MERGE)
            await approval(app, token1, run1, "merge_main", MERGE, approval_id=merge.json()["id"])

            run2 = await started(world, db, dirty.id)
            pr = await approval(app, world.token(run2), run2, "open_pull_request", "Open a PR")
            assert pr.json()["rule"] == "tainted_run"

    rows = audit_rows(db, "agent.gated_action")
    assert len(rows) == 2
    assert [r["details"]["action_class"] for r in rows] == ["merge_main", "open_pull_request"]
    assert [r["details"]["rule"] for r in rows] == ["gated_by_policy", "tainted_run"]
    assert {r["actor_type"] for r in rows} == {"task_token"}


@pytest.mark.req("FR-5.6")
@pytest.mark.wp("P2-05")
async def test_api_key_without_run_refused(  # noqa: PLR0917
    app: FastAPI,
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P2-05-13
    A key with no run (R-31) can never take a gated action or park a run: both ops answer
    200 `denied` with rule `run_token_required` (not 403: Scott decision 43), on the REST
    twin and over MCP, and nothing is queued for the human."""
    from tests._mcp import mcp_call, mcp_running  # noqa: PLC0415
    from tumnis.modules.auth import api as auth  # noqa: PLC0415

    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")
    key = await auth.create_key(
        workspace.ctx,
        auth.KeyIn(name="cron key", scopes=["tasks:read", "tasks:write"]),
        now=clock.now(),
    )
    with human_waits(poll_seconds=1):
        async with relay(db):
            run_id = await started(world, db, task.id)
            via_rest = await approval(app, key.key, run_id, "merge_main", MERGE)
            question = await ask(app, key.key, run_id, "Which footer color?")
            async with mcp_running(app), http_for(app, key.key) as http:
                via_mcp = await mcp_call(
                    http,
                    "request_approval",
                    {
                        "run_id": str(run_id),
                        "action_class": "merge_main",
                        "description": MERGE,
                        "idempotency_key": f"cron-{uuid.uuid4()}",
                    },
                )
    assert via_rest.status_code == 200, via_rest.text
    assert question.status_code == 200, question.text
    for answer in (via_rest.json(), question.json(), via_mcp.data):
        assert answer["status"] == "denied", answer
        assert answer["rule"] == "run_token_required", answer
    assert open_items(db, "approval") == []
    assert open_items(db, "question") == []
