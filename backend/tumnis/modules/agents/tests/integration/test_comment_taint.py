"""A comment an agent writes with its task token carries its run's taint into later
packets (P2-08, SAF-1; P2-02 left task-token comments to P2-08)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from dbos import DBOS

    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]


@pytest.mark.req("SAF-1")
@pytest.mark.wp("P2-08")
async def test_a_task_token_comment_follows_its_run_taint(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
) -> None:
    """A clean task commented on with a tainted run's token gets a tainted packet (the
    comment is a tainted block); commented on with a clean run's token, it stays clean."""
    from tests._mcp import make_world  # noqa: PLC0415
    from tests._taint import Runs, context_item  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.types import ActorRef  # noqa: PLC0415
    from tumnis.modules.agents.api import RunKind  # noqa: PLC0415
    from tumnis.modules.agents.packet_builder import build_packet  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    world = await make_world(workspace, clock)
    actor = ActorRef(f"user:{workspace.user_id}")
    ctx = WorkspaceContext(workspace.id, actor)
    runs = Runs(fake_runner, world)
    outside = await world.task("A", title="Read the Acme thread", label="ai", estimate_minutes=None)
    item = await context_item(ctx, world.projects["A"], tainted=True)
    async with tenant_session(ctx) as s:
        await tasks.link_context_item(s, actor, outside.id, item, now=clock.now())
    inside = await world.task("A", title="Tidy the Acme footer", label="ai", estimate_minutes=None)

    for source, tainted in ((outside, True), (inside, False)):
        caller = await runs.caller(await runs.run_of(source.id))
        target = await world.task(
            "A", title=f"Commented by a run ({tainted})", label="ai", estimate_minutes=None
        )
        async with tenant_session(caller.principal.workspace_context()) as s:
            await tasks.add_comment(
                s, caller.principal.actor, target.id, "Found this in the thread.", now=clock.now()
            )
        packet = await build_packet(
            RunKind.TASK,
            task_id=target.id,
            run_id=target.id,
            profile_id=await runs.setup(),
            ctx=workspace.ctx,
        )
        assert packet.tainted is tainted, source.title
        assert [c["tainted"] for c in packet.body["task"]["comments"]] == [tainted]
