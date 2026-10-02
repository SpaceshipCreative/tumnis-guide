"""MCP helpers for the P2-01 spec tests (the agent surface: `/mcp` and its REST twins).

No assertions live here: spec-guard locks the test bodies, and these helpers adapt to the
registry, the routes and the auth api.

- `mcp_running(app)`: enters the app's MCP session manager (what the lifespan does), for
  tests that drive `/mcp` through `httpx.ASGITransport`, which runs no lifespan.
- `http_for(app, key=None)`: a plain httpx client on the app, with the bearer key when
  given. Unlike `KeyClient` it adds no `Idempotency-Key`: the write-rule tests choose.
- `rpc`, `mcp_tools`, `mcp_call`: one JSON-RPC POST to `/mcp` (stateless, JSON answers),
  the tool list, and one tool call read back as an `Outcome`.
- `rest_call(http, op, args)`: the same call through the op's REST twin (path parameters
  filled from `args`, the rest as query or body; `idempotency_key` sent as the header).
- `World` / `make_world(...)`: projects A and B, each with a Hybrid parent task, made
  through the projects and tasks apis; `SAMPLES[op_name](world, project)` gives an op's
  arguments aimed at that project (a fresh task for ops that update one).
- `Callers`: API keys (cached by scopes and projects), a task token bound to a run in
  project A, and the master key (marked through the caller-facts seam). The key with
  every scope and no project limit IS the master key (the same cache entry): the sweeps'
  writer must reach every write op, master-only ones too (`pause_agents`, P2-09;
  `delegate_task` and `wait_for_task`, P2-06).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

import httpx

from tests._keys import BASE_URL

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

MCP_PATH: Final = "/mcp"
MCP_ACCEPT: Final = "application/json, text/event-stream"
ALL_SCOPES: Final = frozenset(
    {
        "tasks:read",
        "tasks:write",
        "context:read",
        "knowledge:write",
        "drafts:write",
        "delegate",
        "ingest",
    }
)
TOKEN_SCOPES: Final = frozenset({"tasks:read", "tasks:write", "context:read"})
_ids = itertools.count(1)
_surface_runs: set[uuid.UUID] = set()  # the runs running_run made (not real runs)


@dataclass(frozen=True)
class Outcome:
    """What one call answered: the HTTP status, the problem `code` (None on success) and
    the result (a tool's `structuredContent`, a REST body, or the problem object)."""

    status: int
    code: str | None
    data: Any

    @property
    def ok(self) -> bool:
        return self.code is None


@asynccontextmanager
async def mcp_running(app: FastAPI) -> AsyncIterator[None]:
    """The app's MCP session manager, running (the lifespan enters it in production).

    The manager runs in a task of its own: its anyio task group must be entered and left
    by one task, and pytest-asyncio may tear an async fixture down in another task than
    the one that set it up.

    A manager runs once. When the app's lifespan already ran it (a `TestClient` entered
    the app, as the fake runner's does, on its own event loop), a fresh manager serves
    this block on the test's loop and the app's own is put back afterwards."""
    from tumnis.core import mcp_server  # noqa: PLC0415

    manager = app.state.mcp_session_manager
    if getattr(manager, "_has_started", False):  # the SDK's single-use flag (mcp 1.30)
        app.state.mcp_session_manager = mcp_server.session_manager()
        try:
            async with mcp_running(app):
                yield
        finally:
            app.state.mcp_session_manager = manager
        return
    started, stop = asyncio.Event(), asyncio.Event()

    async def run() -> None:
        async with manager.run():
            started.set()
            await stop.wait()

    task = asyncio.create_task(run())
    waiter = asyncio.create_task(started.wait())
    await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
    if task.done():  # it failed to start: raise its error
        waiter.cancel()
        await task
    try:
        yield
    finally:
        stop.set()
        await task


def http_for(
    app: FastAPI, key: str | None = None, *, cookies: dict[str, str] | None = None
) -> httpx.AsyncClient:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=BASE_URL,
        headers=headers,
        cookies=cookies,
    )


async def rpc(
    http: httpx.AsyncClient,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    notification: bool = False,
) -> httpx.Response:
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    if not notification:
        message["id"] = next(_ids)
    return await http.post(
        MCP_PATH,
        json=message,
        headers={"Accept": MCP_ACCEPT, "Content-Type": "application/json"},
    )


def _problem_outcome(response: httpx.Response) -> Outcome:
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text}
    code = body.get("code") if isinstance(body, dict) else None
    return Outcome(response.status_code, code or f"http_{response.status_code}", body)


async def mcp_tools(http: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    """The tool list by name (`tools/list`)."""
    response = await rpc(http, "tools/list", {})
    response.raise_for_status()
    return {tool["name"]: tool for tool in response.json()["result"]["tools"]}


async def mcp_call(http: httpx.AsyncClient, name: str, args: dict[str, Any]) -> Outcome:
    """One `tools/call`; a tool error's code comes from its structured problem, or from
    the problem JSON in its text when it carries no structured content."""
    response = await rpc(http, "tools/call", {"name": name, "arguments": args})
    if response.status_code != 200:
        return _problem_outcome(response)
    body = response.json()
    if "error" in body:
        return Outcome(200, f"jsonrpc_{body['error'].get('code')}", body["error"])
    result = body["result"]
    structured = result.get("structuredContent")
    if not result.get("isError"):
        return Outcome(200, None, structured)
    problem = structured
    if problem is None:
        text = "".join(c.get("text", "") for c in result.get("content", []))
        try:
            problem = json.loads(text)
        except ValueError:
            problem = {"code": "tool_error", "detail": text}
    return Outcome(200, str(problem.get("code") or "tool_error"), problem)


_PATH_PARAM = re.compile(r"{([^}]+)}")


def path_params(rest_path: str) -> list[str]:
    return _PATH_PARAM.findall(rest_path)


def rest_request(op: Any, args: dict[str, Any]) -> tuple[str, str, dict[str, Any], Any, dict]:
    """(method, url, query, body, headers) for the op's REST twin."""
    rest = dict(args)
    key = rest.pop("idempotency_key", None)
    url = op.rest_path
    for name in path_params(op.rest_path):
        url = url.replace("{" + name + "}", str(rest.pop(name)))
    headers = {"Idempotency-Key": key} if key is not None else {}
    if op.rest_method == "GET":
        query = {k: v for k, v in rest.items() if v is not None}
        return op.rest_method, url, query, None, headers
    return op.rest_method, url, {}, rest, headers


async def rest_call(http: httpx.AsyncClient, op: Any, args: dict[str, Any]) -> Outcome:
    method, url, query, body, headers = rest_request(op, args)
    response = await http.request(method, url, params=query or None, json=body, headers=headers)
    if response.status_code >= 400:
        return _problem_outcome(response)
    return Outcome(response.status_code, None, response.json())


def idem() -> str:
    return f"mcp-test-{uuid.uuid4()}"


# --- The world the sweeps act on -----------------------------------------------------------


@dataclass
class World:
    workspace: WorkspaceHandle
    clock: FixedClock
    projects: dict[str, uuid.UUID] = field(default_factory=dict)
    parents: dict[str, uuid.UUID] = field(default_factory=dict)

    async def task(self, project: str, **overrides: Any) -> Any:
        """A fresh root task in the project, made by the workspace's user."""
        from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
        from tumnis.core.types import ActorRef  # noqa: PLC0415
        from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

        actor = ActorRef(f"user:{self.workspace.user_id}")
        overrides.setdefault("title", f"Sweep task {next(_ids)}")
        overrides.setdefault("label", "human")
        overrides.setdefault("estimate_minutes", 45)
        async with tenant_session(WorkspaceContext(self.workspace.id, actor)) as s:
            return await tasks.create_task(
                s,
                actor,
                tasks.TaskCreate(project_id=self.projects[project], **overrides),
                now=self.clock.now(),
            )


async def make_world(workspace: WorkspaceHandle, clock: FixedClock) -> World:
    """Projects A and B (threshold 30 on A), each with a Hybrid parent task."""
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.types import ActorRef  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    world = World(workspace, clock)
    actor = ActorRef(f"user:{workspace.user_id}")
    for name in ("A", "B"):
        async with tenant_session(WorkspaceContext(workspace.id, actor)) as s:
            made = await projects.create_project(
                s,
                actor,
                projects.ProjectCreate(name=f"Surface project {name} {uuid.uuid4().hex[:6]}"),
                now=clock.now(),
            )
            made = await projects.update_project(
                s,
                actor,
                made.id,
                projects.ProjectPatch(subtask_threshold_min=30, version=made.version),
                made.version,
                now=clock.now(),
            )
        world.projects[name] = made.id
        parent = await world.task(name, title=f"Hybrid parent {name}", label="hybrid")
        world.parents[name] = parent.id
    return world


Sample = Callable[[World, str], Awaitable[dict[str, Any]]]


async def _list_tasks(world: World, project: str) -> dict[str, Any]:
    return {"project_id": str(world.projects[project]), "limit": 50}


async def _create_task(world: World, project: str) -> dict[str, Any]:
    return {
        "project_id": str(world.projects[project]),
        "title": f"Made through the surface {next(_ids)}",
        "label": "human",
        "estimate_minutes": 20,
        "idempotency_key": idem(),
    }


async def _update_task_status(world: World, project: str) -> dict[str, Any]:
    task = await world.task(project)
    return {
        "task_id": str(task.id),
        "to": "today",
        "version": task.version,
        "idempotency_key": idem(),
    }


async def _update_estimate(world: World, project: str) -> dict[str, Any]:
    task = await world.task(project)
    return {
        "task_id": str(task.id),
        "estimate_minutes": 90,
        "reason": "Bigger than it looked",
        "version": task.version,
        "idempotency_key": idem(),
    }


async def _get_project_context(world: World, project: str) -> dict[str, Any]:
    return {"project_id": str(world.projects[project])}


async def _search(world: World, project: str) -> dict[str, Any]:
    return {"q": "surface", "project_id": str(world.projects[project]), "limit": 20}


async def _get_task_packet(world: World, project: str) -> dict[str, Any]:
    return {"task_id": str(world.parents[project])}


async def running_run(world: World, project: str) -> uuid.UUID:
    """A running run in the project for P2-04's `post_result`, written as the owner. A
    task token only posts for its own run, so the run is a token's run:

    - a real run of the project that a task token was issued for (P2-08's taint sweep
      calls with a fresh token of a finished `run_skill` run), reopened when it has ended
      and given the fresh AI task when it has none;
    - otherwise the run of the newest task token issued for the project (a fresh AI task's
      run on the project's agent profile, made when there is none);
    - otherwise a new run. Any other caller (a key without a run) may post for it too."""
    from sqlalchemy import text  # noqa: PLC0415

    from tumnis.core import db  # noqa: PLC0415

    project_id = world.projects[project]
    task = await world.task(project, label="ai", estimate_minutes=None)
    async with db.owner_sessionmaker()() as s, s.begin():
        real_run = await s.scalar(
            text(
                "SELECT r.id FROM task_tokens t JOIN runs r ON r.id = t.run_id"
                " WHERE t.workspace_id = :ws AND t.project_id = :p"
                " AND NOT (r.id = ANY(CAST(:made AS uuid[])))"
                " ORDER BY t.created_at DESC, t.id DESC LIMIT 1"
            ),
            {"ws": world.workspace.id, "p": project_id, "made": [str(r) for r in _surface_runs]},
        )
        if real_run is not None:
            await s.execute(
                text(
                    "UPDATE runs SET status = 'running', finished_at = NULL,"
                    " task_id = COALESCE(task_id, :t)"
                    " WHERE id = :id AND (status NOT IN ('running', 'waiting_on_human')"
                    " OR task_id IS NULL)"
                ),
                {"id": real_run, "t": task.id},
            )
            run_id: uuid.UUID = real_run
            return run_id
        token_run = await s.scalar(
            text(
                "SELECT run_id FROM task_tokens WHERE workspace_id = :ws AND project_id = :p"
                " ORDER BY created_at DESC, id DESC LIMIT 1"
            ),
            {"ws": world.workspace.id, "p": project_id},
        )
        run_id = token_run or uuid.uuid4()
        _surface_runs.add(run_id)
        profile_id = await s.scalar(
            text(
                "SELECT id FROM agent_profiles WHERE project_id = :p AND role = 'project'"
                " AND deleted_at IS NULL"
            ),
            {"p": project_id},
        )
        if profile_id is None:
            profile_id = await s.scalar(
                text(
                    "INSERT INTO agent_profiles"
                    " (workspace_id, name, role, project_id, transport, status, created_by)"
                    " VALUES (:ws, :name, 'project', :p, 'daemon', 'ready', 'system')"
                    " RETURNING id"
                ),
                {
                    "ws": world.workspace.id,
                    "name": f"surface-{project_id.hex[:12]}",
                    "p": project_id,
                },
            )
        await s.execute(
            text(
                "INSERT INTO runs (id, workspace_id, task_id, profile_id, kind, status,"
                " started_at, correlation_id, created_by)"
                " VALUES (:id, :ws, :t, :profile, 'task', 'running', now(), :corr, 'system')"
                " ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": run_id,
                "ws": world.workspace.id,
                "t": task.id,
                "profile": profile_id,
                "corr": f"run:{run_id}",
            },
        )
    return run_id


async def _pause_agents(world: World, project: str) -> dict[str, Any]:
    """P2-09: the project's agents paused (a project pause names the project, so the
    project-limited sweeps reach it; a second pause of a paused project answers the
    open one)."""
    return {
        "scope": "project",
        "project_id": str(world.projects[project]),
        "reason": f"Paused through the surface {next(_ids)}",
        "idempotency_key": idem(),
    }


async def _record_human_reply(world: World, project: str) -> dict[str, Any]:
    """P2-16: the answer to a fresh question of a running run (asked through the agents api
    as the run's agent), naming the question's review item and an invented chat message."""
    from sqlalchemy import text  # noqa: PLC0415

    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415

    run_id = await running_run(world, project)
    ctx = WorkspaceContext(world.workspace.id, SYSTEM_ACTOR)
    async with tenant_session(ctx) as s:
        asked = await agents.ask_human(
            s,
            ctx.actor,
            run_id,
            agents.AskHumanIn(run_id=run_id, prompt=f"Which colour? {next(_ids)}"),
            caller_key=None,
            tainted=False,
            now=world.clock.now(),
        )
        item_id = await s.scalar(
            text("SELECT review_item_id FROM questions WHERE id = :q"), {"q": asked.id}
        )
    return {
        "item_kind": "question",
        "item_id": str(item_id),
        "answer": "Navy",
        "channel_message_id": f"1290000000000{next(_ids):06d}",
        "idempotency_key": idem(),
    }


async def _post_result(world: World, project: str) -> dict[str, Any]:
    return {
        "run_id": str(await running_run(world, project)),
        "outcome": "done",
        "summary": f"Posted through the surface {next(_ids)}",
        "idempotency_key": idem(),
    }


async def _ask_human(world: World, project: str) -> dict[str, Any]:
    """P2-05: a question of a running run (a key with no run is answered `denied`)."""
    return {
        "run_id": str(await running_run(world, project)),
        "prompt": f"Which colour? {next(_ids)}",
        "idempotency_key": idem(),
    }


async def _request_approval(world: World, project: str) -> dict[str, Any]:
    """P2-05: an allowed action of a running run (approved at once for a task token)."""
    return {
        "run_id": str(await running_run(world, project)),
        "action_class": "push_feature_branch",
        "description": f"Push the branch {next(_ids)}",
        "idempotency_key": idem(),
    }


async def _ready_profile(s: Any, world: World, project_id: uuid.UUID) -> uuid.UUID:
    """The project's agent profile, made `ready` when there is none (owner session)."""
    from sqlalchemy import text  # noqa: PLC0415

    found: uuid.UUID | None = await s.scalar(
        text(
            "SELECT id FROM agent_profiles WHERE project_id = :p AND role = 'project'"
            " AND deleted_at IS NULL"
        ),
        {"p": project_id},
    )
    if found is not None:
        return found
    made: uuid.UUID = await s.scalar(
        text(
            "INSERT INTO agent_profiles"
            " (workspace_id, name, role, project_id, transport, status, created_by)"
            " VALUES (:ws, :name, 'project', :p, 'daemon', 'ready', 'system') RETURNING id"
        ),
        {"ws": world.workspace.id, "name": f"surface-{project_id.hex[:12]}", "p": project_id},
    )
    return made


async def _delegate_task(world: World, project: str) -> dict[str, Any]:
    """P2-06: a fresh AI task in a project with an agent (the master's tool)."""
    from tumnis.core import db  # noqa: PLC0415

    task = await world.task(project, label="ai", estimate_minutes=None)
    async with db.owner_sessionmaker()() as s, s.begin():
        await _ready_profile(s, world, world.projects[project])
    return {"task_id": str(task.id), "idempotency_key": idem()}


async def _wait_for_task(world: World, project: str) -> dict[str, Any]:
    """P2-06: a delegation whose run has ended (succeeded), so the wait answers at once
    and both doors give the same answer."""
    from sqlalchemy import text  # noqa: PLC0415

    from tumnis.core import db  # noqa: PLC0415

    project_id = world.projects[project]
    task = await world.task(project, label="ai", estimate_minutes=None)
    delegation_id = uuid.uuid4()
    async with db.owner_sessionmaker()() as s, s.begin():
        profile_id = await _ready_profile(s, world, project_id)
        await s.execute(
            text(
                "INSERT INTO runs (id, workspace_id, task_id, profile_id, kind, status,"
                " started_at, finished_at, workflow_id, correlation_id, delegation_id,"
                " created_by) VALUES (:id, :ws, :t, :profile, 'task', 'succeeded', now(),"
                " now(), :wf, :corr, :id, 'system')"
            ),
            {
                "id": delegation_id,
                "ws": world.workspace.id,
                "t": task.id,
                "profile": profile_id,
                "wf": str(delegation_id),
                "corr": f"run:{delegation_id}",
            },
        )
        await s.execute(
            text(
                "INSERT INTO delegations (id, workspace_id, child_task_id, project_id, depth,"
                " delegated_at, created_by) VALUES (:id, :ws, :t, :p, 1, now(), 'system')"
            ),
            {"id": delegation_id, "ws": world.workspace.id, "t": task.id, "p": project_id},
        )
    return {"delegation_id": str(delegation_id), "timeout_seconds": 1}


async def _search_knowledge(world: World, project: str) -> dict[str, Any]:
    """P2-17: a search of the project's documents and the workspace knowledge base."""
    return {"q": "surface", "project_id": str(world.projects[project]), "limit": 20}


async def _get_document(world: World, project: str) -> dict[str, Any]:
    """P2-17: a fresh text entry of the project, written by the workspace's user."""
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.types import ActorRef  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    actor = ActorRef(f"user:{world.workspace.user_id}")
    async with tenant_session(WorkspaceContext(world.workspace.id, actor)) as s:
        made = await knowledge.create_text_entry(
            s, world.projects[project], f"Surface notes {next(_ids)}", "Notes on the surface."
        )
    return {"document_id": str(made.id)}


async def _add_document(world: World, project: str) -> dict[str, Any]:
    """P2-17: a Markdown document for the project's knowledge base."""
    return {
        "project_id": str(world.projects[project]),
        "title": f"Added through the surface {next(_ids)}",
        "body_md": "# Findings\n\nWhat the surface found.",
        "tags": ["surface"],
        "idempotency_key": idem(),
    }


async def _get_project_digest(world: World, project: str) -> dict[str, Any]:
    return {"project_id": str(world.projects[project]), "limit": 50}


async def _get_workspace_digest(world: World, project: str) -> dict[str, Any]:
    return {"limit": 50}


# One entry per registered op; the sweeps fail on an op without one ("add a sample").
SAMPLES: Final[dict[str, Sample]] = {
    "list_tasks": _list_tasks,
    "create_task": _create_task,
    "update_task_status": _update_task_status,
    "update_estimate": _update_estimate,
    "get_project_context": _get_project_context,
    "search": _search,
    "get_task_packet": _get_task_packet,
    "post_result": _post_result,
    "pause_agents": _pause_agents,
    "record_human_reply": _record_human_reply,
    "ask_human": _ask_human,
    "request_approval": _request_approval,
    "search_knowledge": _search_knowledge,
    "get_document": _get_document,
    "add_document": _add_document,
    "get_project_digest": _get_project_digest,
    "get_workspace_digest": _get_workspace_digest,
    "delegate_task": _delegate_task,
    "wait_for_task": _wait_for_task,
}


# --- Callers ---------------------------------------------------------------------------------


@dataclass
class Callers:
    """Credentials for the sweeps, made through the auth api in the world's workspace."""

    app: FastAPI
    world: World
    _keys: dict[tuple[frozenset[str], frozenset[uuid.UUID] | None], tuple[str, uuid.UUID]] = field(
        default_factory=dict
    )
    master_key_id: uuid.UUID | None = None

    def _ctx(self) -> Any:
        from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
        from tumnis.core.types import ActorRef  # noqa: PLC0415

        return WorkspaceContext(
            self.world.workspace.id, ActorRef(f"user:{self.world.workspace.user_id}")
        )

    async def key(
        self, scopes: frozenset[str], projects: frozenset[uuid.UUID] | None = None
    ) -> tuple[str, uuid.UUID]:
        """(secret, key id) of a key with exactly these scopes (and project limit)."""
        cache_key = (frozenset(scopes), projects)
        if cache_key == (ALL_SCOPES, None) and self.master_key_id is None:
            await self.master_key()
        if cache_key not in self._keys:
            created = await self._create(scopes, projects)
            self._keys[cache_key] = (created.key, created.id)
        return self._keys[cache_key]

    async def _create(self, scopes: frozenset[str], projects: frozenset[uuid.UUID] | None) -> Any:
        from tumnis.modules.auth import api as auth  # noqa: PLC0415

        return await auth.create_key(
            self._ctx(),
            auth.KeyIn(
                name=f"sweep {next(_ids)}",
                scopes=sorted(scopes),
                project_ids=None if projects is None else sorted(projects, key=str),
            ),
            now=self.world.clock.now(),
        )

    async def task_token(self, project: str = "A", run_id: uuid.UUID | None = None) -> str:
        """A task token for a run in the project, with `TOKEN_SCOPES`."""
        from tumnis.modules.auth import api as auth  # noqa: PLC0415

        _secret, parent_id = await self.key(ALL_SCOPES)
        return await auth.issue_task_token(
            self._ctx(),
            run_id=run_id or uuid.uuid4(),
            project_id=self.world.projects[project],
            api_key_id=parent_id,
            scopes=TOKEN_SCOPES,
            now=self.world.clock.now(),
        )

    async def master_key(self) -> str:
        """A key with every scope that the caller-facts seam marks as the master's (P2-02
        links it to the master profile; until then the test registers the fact)."""
        from tumnis.core import agent_surface  # noqa: PLC0415

        cache_key = (ALL_SCOPES, None)
        if cache_key not in self._keys:
            created = await self._create(ALL_SCOPES, None)
            self._keys[cache_key] = (created.key, created.id)
        secret, key_id = self._keys[cache_key]
        if self.master_key_id == key_id:
            return secret
        self.master_key_id = key_id

        async def facts(principal: Any) -> Any:
            if principal.subject_id == key_id:
                return agent_surface.CallerFacts(profile_id=None, is_master=True, run_id=None)
            return None

        agent_surface.register_caller_facts("test-master", facts)
        return secret

    def close(self) -> None:
        """Drops the master fact the test registered."""
        if self.master_key_id is not None:
            from tumnis.core import agent_surface  # noqa: PLC0415

            agent_surface.unregister_caller_facts("test-master")
