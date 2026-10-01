"""Every create op on the agent surface propagates taint (P2-08, SAF-1), swept over the op
registry, so an op registered later (P3-07's `create_proposal`, for example) is covered
the day it registers.

A create op is a write that updates no existing record. Each one answers `tainted`, and
its output is tainted when it is called with a tainted run's token, when its input names
a tainted `parent_id`, and when its input's `context_item_ids` hold a tainted item (the
last two called by a clean run's token, so the input alone carries the taint).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

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


def _create_ops() -> list[Any]:
    from tumnis.core import agent_surface  # noqa: PLC0415
    from tumnis.wiring import load_mcp  # noqa: PLC0415

    load_mcp()
    return [op for op in agent_surface.ops() if op.write and not op.updates_existing]


async def _master_caller(workspace: WorkspaceHandle, clock: FixedClock) -> Any:
    """The master key's caller (Scott decision 35): a master-only op (`pause_agents`, P2-09)
    refuses a project run's token, so the sweep calls it as the master. The key has no run,
    so what it writes is tainted (R-31)."""
    from tests._mcp import ALL_SCOPES  # noqa: PLC0415
    from tumnis.core import agent_surface  # noqa: PLC0415
    from tumnis.core.principal import Principal  # noqa: PLC0415
    from tumnis.modules.auth import api as auth  # noqa: PLC0415

    key = await auth.create_key(
        workspace.ctx,
        auth.KeyIn(name="taint sweep master", scopes=sorted(ALL_SCOPES)),
        now=clock.now(),
    )

    async def facts(principal: Any) -> Any:
        if principal.subject_id == key.id:
            return agent_surface.CallerFacts(profile_id=None, is_master=True, run_id=None)
        return None

    agent_surface.register_caller_facts("taint-sweep-master", facts)
    try:
        principal = await auth.authenticate_bearer(key.key, now=clock.now())
        assert isinstance(principal, Principal)
        return await agent_surface.resolve_caller(principal)
    finally:
        agent_surface.unregister_caller_facts("taint-sweep-master")


@pytest.mark.req("SAF-1")
@pytest.mark.wp("P2-08")
async def test_every_create_op_propagates_taint(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
) -> None:
    """T-P2-08-07
    For each registry op that creates a record: its output model has `tainted`; called
    with a tainted run's token its output is tainted; with a tainted `parent_id` in its
    input, or a tainted item in its `context_item_ids`, its output is tainted too.
    """
    from tests._mcp import SAMPLES, make_world  # noqa: PLC0415
    from tests._taint import Runs, context_item  # noqa: PLC0415
    from tumnis.core import agent_surface  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.types import ActorRef  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    creates = _create_ops()
    assert creates, "the registry has no create op"
    world = await make_world(workspace, clock)
    actor = ActorRef(f"user:{workspace.user_id}")
    ctx = WorkspaceContext(workspace.id, actor)
    runs = Runs(fake_runner, world)

    dirty_item = await context_item(ctx, world.projects["A"], tainted=True)
    outside = await world.task(
        "A", title="Reply to the Acme thread", label="ai", estimate_minutes=None
    )
    async with tenant_session(ctx) as s:
        await tasks.link_context_item(s, actor, outside.id, dirty_item, now=clock.now())
    tainted_caller = await runs.caller(await runs.run_of(outside.id))
    clean_caller = await runs.caller(uuid.uuid4())  # a run with nothing outside in it
    master_caller = await _master_caller(workspace, clock)  # for master-only ops

    async def call(op: Any, caller: Any, args: dict[str, Any]) -> dict[str, Any]:
        answer = await agent_surface.invoke(op, caller, args, door="mcp", now=clock.now())
        body: dict[str, Any] = answer.model_dump()
        return body

    for op in creates:
        fields = op.input_model.model_fields
        assert "tainted" in op.output_model.model_fields, f"{op.name} answers no taint"

        writer = master_caller if op.master_only else tainted_caller
        made = await call(op, writer, await SAMPLES[op.name](world, "A"))
        assert made["tainted"] is True, (op.name, "tainted run", made)

        if "parent_id" in fields:
            parent = await world.task("A", title=f"Outside parent {uuid.uuid4().hex[:6]}")
            async with tenant_session(ctx) as s:
                await tasks.link_context_item(s, actor, parent.id, dirty_item, now=clock.now())
            args = await SAMPLES[op.name](world, "A")
            args["parent_id"] = str(parent.id)
            made = await call(op, clean_caller, args)
            assert made["tainted"] is True, (op.name, "tainted parent", made)

        if "context_item_ids" in fields:
            args = await SAMPLES[op.name](world, "A")
            args["context_item_ids"] = [str(dirty_item)]
            made = await call(op, clean_caller, args)
            assert made["tainted"] is True, (op.name, "tainted context item", made)
