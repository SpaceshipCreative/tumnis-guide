"""Taint on the task paths (P2-08, SAF-1, design decision 14): linking outside content to a
task raises its taint and unlinking never lowers it, and a task an agent creates with a
tainted run's token is tainted even when it links nothing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._pg import DbUrls
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
@pytest.mark.xfail(strict=True, reason="spec:P2-08")
async def test_linking_tainted_item_taints_task_and_unlinking_keeps_it(
    workspace: WorkspaceHandle, clock: FixedClock, db: DbUrls
) -> None:
    """T-P2-08-04
    A person's task starts untainted and stays so when an untainted item is linked.
    Linking a tainted item raises its taint (`raise_only`), stored on the row; unlinking
    that item keeps the task tainted. Attaching a tainted item to a task through
    `integrations.api.link_context` (owner `task`) raises that task's taint too.
    """
    from tests._mcp import make_world  # noqa: PLC0415
    from tests._taint import context_item, taint_of  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.types import ActorRef  # noqa: PLC0415
    from tumnis.modules.integrations import api as integrations  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    world = await make_world(workspace, clock)
    project = world.projects["A"]
    actor = ActorRef(f"user:{workspace.user_id}")
    ctx = WorkspaceContext(workspace.id, actor)
    clean = await context_item(ctx, project, tainted=False)
    dirty = await context_item(ctx, project, tainted=True)

    task = await world.task("A", title="Answer the Acme footer question")
    assert task.tainted is False
    async with tenant_session(ctx) as s:
        linked = await tasks.link_context_item(s, actor, task.id, clean, now=clock.now())
    assert linked.tainted is False
    assert taint_of(db, "tasks", task.id) is False

    async with tenant_session(ctx) as s:
        linked = await tasks.link_context_item(s, actor, task.id, dirty, now=clock.now())
    assert linked.tainted is True
    assert taint_of(db, "tasks", task.id) is True
    async with tenant_session(ctx) as s:
        assert (await tasks.get_task(s, task.id)).tainted is True

    async with tenant_session(ctx) as s:
        await tasks.unlink_context_item(  # type: ignore[attr-defined]
            s, actor, task.id, dirty, now=clock.now()
        )
        assert dirty not in await tasks.context_item_ids(s, task.id)
    assert taint_of(db, "tasks", task.id) is True

    other = await world.task("A", title="Check the Acme cookie banner")
    assert other.tainted is False
    await integrations.link_context(
        ctx,
        owner_type="task",
        owner_id=other.id,
        target_type="url",
        target_url="https://example.com/cookie-banner-thread",
        added_by=actor,
    )
    assert taint_of(db, "tasks", other.id) is True


@pytest.mark.req("SAF-1")
@pytest.mark.wp("P2-08")
@pytest.mark.xfail(strict=True, reason="spec:P2-08")
async def test_task_created_by_tainted_run_is_tainted(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P2-08-05
    A task run whose packet carries outside content is tainted (`runs.tainted` from the
    packet). A task created through `create_task` with a token of that run, linking no
    context item and naming no parent, is tainted; one created the same way with a token
    of a clean run is not.
    """
    from tests._mcp import make_world  # noqa: PLC0415
    from tests._taint import Runs, context_item, taint_of  # noqa: PLC0415
    from tumnis.core import agent_surface  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.types import ActorRef  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415
    from tumnis.wiring import load_mcp  # noqa: PLC0415

    load_mcp()
    world = await make_world(workspace, clock)
    actor = ActorRef(f"user:{workspace.user_id}")
    ctx = WorkspaceContext(workspace.id, actor)
    runs = Runs(fake_runner, world)

    outside = await world.task(
        "A", title="Reply to the Acme invoice email", label="ai", estimate_minutes=None
    )
    item = await context_item(ctx, world.projects["A"], tainted=True)
    async with tenant_session(ctx) as s:
        await tasks.link_context_item(s, actor, outside.id, item, now=clock.now())
    inside = await world.task("A", title="Tidy the Acme footer", label="ai", estimate_minutes=None)

    tainted_run = await runs.run_of(outside.id)
    clean_run = await runs.run_of(inside.id)
    assert taint_of(db, "runs", tainted_run) is True
    assert taint_of(db, "runs", clean_run) is False

    create = agent_surface.get_op("create_task")
    made: dict[str, dict[str, Any]] = {}
    for name, run_id in (("tainted", tainted_run), ("clean", clean_run)):
        caller = await runs.caller(run_id)
        answer = await agent_surface.invoke(
            create,
            caller,
            {
                "project_id": str(world.projects["A"]),
                "title": f"Follow-up from the {name} run",
                "label": "ai",
                "idempotency_key": f"taint-run-{name}-{run_id}",
            },
            door="mcp",
            now=clock.now(),
        )
        made[name] = answer.model_dump()
    assert made["tainted"]["tainted"] is True
    assert taint_of(db, "tasks", made["tainted"]["id"]) is True
    assert made["clean"]["tainted"] is False
    assert taint_of(db, "tasks", made["clean"]["id"]) is False
