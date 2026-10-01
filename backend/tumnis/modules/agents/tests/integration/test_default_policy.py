"""The approval policy a new project stores (P0-17) and the one `approval_need` knows
(P2-05) use one vocabulary of action classes: a default-policy project gates every class
of its stored gated list and allows every class of its stored allowed list. An allowed
class is approved at once with one `approval.auto` audit row (SEC-3).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tumnis.modules.agents.tests.integration._human import (
    approval,
    audit_rows,
    human_waits,
    started,
)
from tumnis.modules.agents.tests.integration._runs import relay, run_world

if TYPE_CHECKING:
    from dbos import DBOS
    from fastapi import FastAPI

    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]


@pytest.mark.req("FR-5.6")
@pytest.mark.wp("P2-05")
async def test_default_policy_project_gates_each_default_class(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """A project created with the default policy: its stored lists equal the agents
    module's defaults, every stored gated class needs approval (`gated_by_policy`) and
    every stored allowed class goes through (`allowed_by_policy`)."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415
    from tumnis.modules.agents.rules import DEFAULT_POLICY  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Tidy the footer")
    async with tenant_session(world.ctx) as s:
        stored = await projects.get_policy(s, world.project_id)
    assert frozenset(stored.gated) == DEFAULT_POLICY.gated
    assert frozenset(stored.allowed) == DEFAULT_POLICY.allowed
    async with relay(db):
        run_id = await started(world, db, task.id)
        for action in stored.gated:
            verdict = await agents.check_action(world.ctx, run_id, action)
            assert verdict == agents.ApprovalRequired("gated_by_policy"), action
        for action in stored.allowed:
            verdict = await agents.check_action(world.ctx, run_id, action)
            assert verdict == agents.Allowed("allowed_by_policy"), action


@pytest.mark.req("SEC-3")
@pytest.mark.wp("P2-05")
async def test_allowed_class_writes_one_auto_approval_row(  # noqa: PLR0917
    app: FastAPI,
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """An allowed class on a clean run is approved at once with one `approval.auto` row
    (the task token as actor, the class and the rule); a re-send writes nothing more, and
    `read` (no row at all) writes none."""
    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")
    with human_waits(poll_seconds=0):
        async with relay(db):
            run_id = await started(world, db, task.id)
            token = world.token(run_id)
            pushed = await approval(app, token, run_id, "push_feature_branch", "Push fix-footer")
            assert pushed.json()["status"] == "approved", pushed.text
            again = await approval(
                app, token, run_id, "push_feature_branch", "Push", approval_id=pushed.json()["id"]
            )
            assert again.json()["status"] == "approved", again.text
            read = await approval(app, token, run_id, "read", "Read the footer")
            assert read.json()["status"] == "approved", read.text

    rows = audit_rows(db, "approval.auto")
    assert len(rows) == 1, rows
    assert rows[0]["actor_type"] == "task_token"
    assert rows[0]["details"] == {
        "action_class": "push_feature_branch",
        "rule": "allowed_by_policy",
        "project_id": str(world.project_id),
    }
