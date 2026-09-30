"""A2.3 · Approval gates and tainted runs (phase 2 acceptance, committed red on the phase's
first day).

A gated action waits for a human's approval with a reason; an allowed one goes through at
once; on a tainted run every action needs approval. Turns green with P2-05 (the taint flag
comes from P2-02).

Fixtures: `db`, `dbos`, `clock`, `fakes`, `app`, `workspace`, `fake_runner`,
`session_client`. The plan lists `seed`; the `session_client` and `fake_runner` serve the
`workspace` fixture's workspace, so `_phase2.arrange_world` makes the plan's rows there.
The test plays the agent: it calls `request_approval` over MCP with the run's task token.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tests.acceptance._phase2 import (
    arrange_world,
    audit,
    decide,
    human_wait_poll_seconds,
    relay,
    review_items,
    run_task,
    tool,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.req("A2.3", "FR-5.6", "SAF-1"),
]


@pytest.mark.wp("P2-05")
@pytest.mark.xfail(strict=True, reason="spec:P2-05")
async def test_gated_action_waits_for_approval_and_tainted_run_gates_everything(  # noqa: PLR0917
    db: DbUrls,
    dbos: Any,
    clock: FixedClock,
    fakes: Any,
    app: FastAPI,
    workspace: WorkspaceHandle,
    fake_runner: FakeRunnerFactory,
    session_client: SessionClient,
) -> None:
    """A2.3
    Given two AI tasks in one project, T1 untainted and T2 linked to a tainted email, and
    the default FR-5.6 policy: on T1's run `push_feature_branch` is approved at once
    (`allowed_by_policy`, no review item) while `merge_main` is pending with one `approval`
    review item; approving it with a reason makes the re-send `approved` and writes one
    `approval.granted` audit row with the reason. On T2's run the allowed
    `open_pull_request` is still pending with `rule="tainted_run"`; a blank reason is 422
    `reason_required`; denying it makes the re-send `denied` with one `approval.denied`
    audit row.
    """
    human_wait_poll_seconds(2)
    world = await arrange_world(fake_runner, workspace, clock)
    t1 = await world.ai_task("Fix footer link")
    t2 = await world.ai_task("Update the footer colours", tainted=True)

    async with relay(db):
        # 1. Run T1: a feature-branch push, then a merge to main.
        run1 = await run_task(session_client, t1)
        await world.delivered(run1)
        token1 = world.token(run1)
        push = await tool(
            world.runner,
            token1,
            "request_approval",
            {
                "run_id": str(run1),
                "action_class": "push_feature_branch",
                "description": "Push fix-footer",
            },
        )
        assert push.ok, push
        assert push.data["status"] == "approved"
        assert push.data["rule"] == "allowed_by_policy"
        assert await review_items(session_client, "approval") == []

        merge = await tool(
            world.runner,
            token1,
            "request_approval",
            {"run_id": str(run1), "action_class": "merge_main", "description": "Merge fix-footer"},
        )
        assert merge.ok, merge
        assert merge.data["status"] == "pending"
        [merge_item] = await review_items(session_client, "approval")

        # 2. Approve the merge with a reason; the re-send returns approved.
        approved = await decide(
            session_client, merge_item, "approve", {"reason": "Checked the diff"}
        )
        assert approved.status_code == 200, approved.text
        again = await tool(
            world.runner,
            token1,
            "request_approval",
            {
                "run_id": str(run1),
                "action_class": "merge_main",
                "description": "Merge fix-footer",
                "approval_id": merge.data["id"],
            },
        )
        assert again.ok, again
        assert again.data["status"] == "approved"
        granted = audit(db, "approval.granted")
        assert len(granted) == 1
        assert granted[0]["reason"] == "Checked the diff"

        # 3. Run T2 (tainted): even an allowed action needs approval.
        run2 = await run_task(session_client, t2)
        await world.delivered(run2)
        token2 = world.token(run2)
        pr = await tool(
            world.runner,
            token2,
            "request_approval",
            {
                "run_id": str(run2),
                "action_class": "open_pull_request",
                "description": "Open a PR for the colours",
            },
        )
        assert pr.ok, pr
        assert pr.data["status"] == "pending"
        assert pr.data["rule"] == "tainted_run"
        [pr_item] = await review_items(session_client, "approval")

        # No approval can be decided without a reason.
        blank = await decide(session_client, pr_item, "deny", {"reason": ""})
        assert blank.status_code == 422
        assert blank.json()["code"] == "reason_required"

        # 4. Deny with a reason; the re-send returns denied.
        denied = await decide(session_client, pr_item, "deny", {"reason": "Not from this client"})
        assert denied.status_code == 200, denied.text
        after = await tool(
            world.runner,
            token2,
            "request_approval",
            {
                "run_id": str(run2),
                "action_class": "open_pull_request",
                "description": "Open a PR for the colours",
                "approval_id": pr.data["id"],
            },
        )
        assert after.ok, after
        assert after.data["status"] == "denied"
        refused = audit(db, "approval.denied")
        assert len(refused) == 1
        assert refused[0]["reason"] == "Not from this client"
