"""Taint flows forward through every path that creates work (P2-08, SAF-1, design
decision 14): a property test over random creation graphs, across tasks, knowledge and
agents, through their real `api.py` functions and a real run on the fake runner.

Leaves are context items (random taint) and tasks a person writes (untainted). Inner nodes
are a task linking items (at create or after), a subtask of a root task (optionally
linking one more item), a run of a task, a task created with that run's token (optionally
under a root task) and a document added by that run. Every node's stored `tainted`, read
back from the database, equals the OR of its sources' stored taint.

Proposals are left out until P3-07 brings their table (the plan's `proposals` belong to
P3-02 to P3-07); `integrations.api.proposal_taint` is their create path's rule.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._mcp import World
    from tests._pg import DbUrls
    from tests._taint import Runs
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.slow,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
    # The strategies draw nothing from `random`; DBOS's queue worker threads (the `dbos`
    # fixture, dbos 3.1.0) call `random.uniform` for their polling jitter on the global
    # generator, and when one does so during a draw Hypothesis takes it for the strategy's
    # and warns, which fails the example only some of the time (a FlakyFailure).
    pytest.mark.filterwarnings(
        "ignore:Do not use the `random` module inside strategies"
        ":hypothesis.errors.HypothesisDeprecationWarning"
    ),
]

KINDS: Final = ("item", "user_task", "linked_task", "subtask", "run", "run_task", "run_doc")
TABLES: Final = {
    "item": "context_items",
    "user_task": "tasks",
    "linked_task": "tasks",
    "subtask": "tasks",
    "run": "runs",
    "run_task": "tasks",
    "run_doc": "documents",
}
MAX_RUNS: Final = 3  # real runs per graph; a further run node becomes a person's task

Step = tuple[str, list[int], bool]
STEPS: Final = st.lists(
    st.tuples(
        st.sampled_from(KINDS),
        st.lists(st.integers(min_value=0, max_value=10**6), min_size=1, max_size=3),
        st.booleans(),
    ),
    min_size=3,
    max_size=15,
)


@dataclass
class Node:
    kind: str
    id: uuid.UUID
    sources: list[int] = field(default_factory=list)  # indices of earlier nodes
    leaf_taint: bool | None = None  # a leaf's own taint
    root: bool = False  # a task that can take subtasks


@dataclass
class Graph:
    world: World
    runs: Runs
    nodes: list[Node] = field(default_factory=list)

    def of(self, *kinds: str, root: bool = False) -> list[int]:
        return [i for i, n in enumerate(self.nodes) if n.kind in kinds and (n.root or not root)]


_graphs: dict[uuid.UUID, tuple[World, Runs]] = {}  # one world and runner per test workspace


async def _setting(
    fake_runner: FakeRunnerFactory, workspace: WorkspaceHandle, clock: FixedClock
) -> tuple[World, Runs]:
    from tests._mcp import make_world  # noqa: PLC0415
    from tests._taint import Runs  # noqa: PLC0415

    if workspace.id not in _graphs:
        world = await make_world(workspace, clock)
        _graphs[workspace.id] = (world, Runs(fake_runner, world))
    return _graphs[workspace.id]


def _pick(choices: list[int], picks: list[int]) -> list[int]:
    return sorted({choices[p % len(choices)] for p in picks})


async def _add(graph: Graph, step: Step) -> None:  # noqa: PLR0912, PLR0915  # one branch per node kind
    from tests._taint import context_item  # noqa: PLC0415
    from tumnis.core import agent_surface  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.types import ActorRef  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    kind, picks, flag = step
    world, now = graph.world, graph.world.clock.now()
    project = world.projects["A"]
    actor = ActorRef(f"user:{world.workspace.user_id}")
    ctx = WorkspaceContext(world.workspace.id, actor)
    items, roots = graph.of("item"), graph.of("user_task", "linked_task", "run_task", root=True)
    task_nodes = graph.of("user_task", "linked_task", "subtask", "run_task")
    run_nodes = graph.of("run")
    if kind in ("linked_task",) and not items:
        kind = "item"
    if kind == "subtask" and not roots:
        kind = "user_task"
    if kind == "run" and (not task_nodes or len(run_nodes) >= MAX_RUNS):
        kind = "user_task"
    if kind in ("run_task", "run_doc") and not run_nodes:
        kind = "item"

    if kind == "item":
        graph.nodes.append(
            Node(kind, await context_item(ctx, project, tainted=flag), leaf_taint=flag)
        )
    elif kind == "user_task":
        made = await world.task("A")
        graph.nodes.append(Node(kind, made.id, leaf_taint=False, root=True))
    elif kind == "linked_task":
        chosen = _pick(items, picks)
        ids = [graph.nodes[i].id for i in chosen]
        async with tenant_session(ctx) as s:
            data = tasks.TaskCreate(
                project_id=project, title=f"Linked {uuid.uuid4().hex[:6]}", label="ai"
            )
            if flag:
                made = await tasks.create_task(s, actor, data, now=now, context_item_ids=ids)
            else:
                made = await tasks.create_task(s, actor, data, now=now)
                for item_id in ids:
                    await tasks.link_context_item(s, actor, made.id, item_id, now=now)
        graph.nodes.append(Node(kind, made.id, sources=chosen, root=True))
    elif kind == "subtask":
        parent = _pick(roots, picks[:1])[0]
        extra = _pick(items, picks[1:]) if flag and items and len(picks) > 1 else []
        async with tenant_session(ctx) as s:
            made = await tasks.create_task(
                s,
                actor,
                tasks.TaskCreate(
                    project_id=project,
                    parent_id=graph.nodes[parent].id,
                    title=f"Subtask {uuid.uuid4().hex[:6]}",
                    label="ai",
                ),
                now=now,
            )
            for i in extra:
                await tasks.link_context_item(s, actor, made.id, graph.nodes[i].id, now=now)
        graph.nodes.append(Node(kind, made.id, sources=[parent, *extra]))
    elif kind == "run":
        task = _pick(task_nodes, picks[:1])[0]
        run_id = await graph.runs.run_of(graph.nodes[task].id)
        graph.nodes.append(Node(kind, run_id, sources=[task]))
    elif kind == "run_task":
        run = _pick(run_nodes, picks[:1])[0]
        parent = _pick(roots, picks[1:])[0] if flag and roots and len(picks) > 1 else None
        caller = await graph.runs.caller(graph.nodes[run].id)
        raw: dict[str, Any] = {
            "project_id": str(project),
            "title": f"From a run {uuid.uuid4().hex[:6]}",
            "label": "ai",
            "idempotency_key": f"taint-graph-{uuid.uuid4()}",
        }
        if parent is not None:
            raw["parent_id"] = str(graph.nodes[parent].id)
        answer = await agent_surface.invoke(
            agent_surface.get_op("create_task"), caller, raw, door="mcp", now=now
        )
        made_id = uuid.UUID(str(answer.model_dump()["id"]))
        sources = [run] if parent is None else [run, parent]
        graph.nodes.append(Node(kind, made_id, sources=sources, root=parent is None))
    else:  # run_doc
        run = _pick(run_nodes, picks[:1])[0]
        caller = await graph.runs.caller(graph.nodes[run].id)
        async with tenant_session(caller.principal.workspace_context()) as s:
            added = await knowledge.add_document(
                s,
                caller,
                project_id=project,
                title=f"Run notes {uuid.uuid4().hex[:6]}",
                body_markdown="What the run found.",
                now=now,
            )
        graph.nodes.append(Node(kind, added.id, sources=[run]))


@pytest.mark.req("SAF-1")
@pytest.mark.wp("P2-08")
@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(steps=STEPS)
async def test_taint_flows_through_random_creation_graphs(  # noqa: PLR0917  # the fixtures it needs
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    steps: list[Step],
) -> None:
    """T-P2-08-01
    In any creation graph of 3 to 15 nodes (context items, a person's tasks, tasks
    linking items, subtasks, runs, tasks and documents made by a run), each node's stored
    taint equals the OR of its sources' stored taint; a leaf keeps its own.
    """
    from tests._taint import taint_of  # noqa: PLC0415
    from tumnis.wiring import load_mcp  # noqa: PLC0415

    load_mcp()
    world, runs = await _setting(fake_runner, workspace, clock)
    graph = Graph(world, runs)
    for step in steps:
        await _add(graph, step)

    stored = [taint_of(db, TABLES[node.kind], node.id) for node in graph.nodes]
    for i, node in enumerate(graph.nodes):
        expected = (
            node.leaf_taint if node.leaf_taint is not None else any(stored[s] for s in node.sources)
        )
        assert stored[i] is expected, (i, node, [graph.nodes[s] for s in node.sources])
