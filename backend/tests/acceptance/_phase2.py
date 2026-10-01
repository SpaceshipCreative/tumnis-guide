"""Shared helpers for the phase 2 acceptance suite (A2.2 to A2.5), committed red with it.

Helpers hold no assertions: they arrange rows, drive the agent's side of a run and read
state, so the work packages that build the pieces may adjust them without touching a
locked test body (spec-guard locks the tests, not this file). Everything under
`tumnis.modules` is imported inside the functions: much of it lands with phase 2, and
collection must not depend on it.

The world lives in the `workspace` fixture's workspace, the one `session_client`,
`key_client` and `fake_runner` serve (the seed workspace is another one, and the plan's
names `Acme site` and `acme-site` are not in the seed set yet): `arrange_world` makes the
project, its agent profile on a fake runner speaking protocol 2 (P2-07) and the key its
runs issue task tokens from (P2-02), and `master` adds the master profile with its key.

The fake runner answers nothing on its own here: the test plays the agent. It reads the
task token from the `run` packet the runner received, calls tools over MCP with it
(through the runner's TestClient, whose lifespan runs the MCP session manager, on a
thread so the test's loop keeps serving the relay), and sends protocol-2 frames
(`stream`) back over the runner's socket. The names follow the plan: `POST
/v1/tasks/{id}/run`, `GET /v1/runs/{id}` (P2-04), `ask_human`, `request_approval` and
`settings.human_wait_poll_seconds` (P2-05), `delegate_task` and `wait_for_task` (P2-06),
`POST /v1/agents/pause` and `resume` (P2-09); best readings for the owning WPs to adjust.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    import httpx

    from tests._auth import SessionClient
    from tests._mcp import Outcome
    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunner, FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.core.tenancy import WorkspaceContext

ACME: Final = "Acme site"
ACME_AGENT: Final = "acme-site"
MASTER: Final = "tumnis-master"
# Protocol 2 (R-25): the daemon lists 2 and advertises stream, cancel and upload_artifact.
V2_CAPABILITIES: Final = ["run", "health", "stream", "cancel", "upload_artifact", "worktree"]
PROFILE_KEY_SCOPES: Final = ["tasks:read", "tasks:write", "context:read"]
MASTER_KEY_SCOPES: Final = ["tasks:read", "tasks:write", "delegate"]
# A tainted email linked to a task (outside content, SAF-1); an invented address.
EMAIL_URL: Final = "https://mail.example.com/acme/threads/footer-colours"
T0: Final = "2026-03-09T12:00:00Z"

Json = dict[str, Any]


# --- The world -----------------------------------------------------------------------------


def user_ctx(workspace: WorkspaceHandle) -> WorkspaceContext:
    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.core.types import ActorRef  # noqa: PLC0415

    return WorkspaceContext(workspace.id, ActorRef(f"user:{workspace.user_id}"))


@dataclass
class World:
    workspace: WorkspaceHandle
    clock: FixedClock
    runner: FakeRunner
    project_id: uuid.UUID
    profile_id: uuid.UUID
    master_profile_id: uuid.UUID | None = None
    master_key: str | None = None
    projects: dict[str, uuid.UUID] = field(default_factory=dict)

    async def ai_task(
        self, title: str, *, tainted: bool = False, project_id: uuid.UUID | None = None
    ) -> uuid.UUID:
        """An AI task in Today with a first action and acceptance criteria, made by the
        workspace's user; `tainted` links a tainted email to it (outside content)."""
        from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
        from tumnis.modules.integrations import api as integrations  # noqa: PLC0415
        from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

        ctx = user_ctx(self.workspace)
        async with tenant_session(ctx) as s:
            task = await tasks.create_task(
                s,
                ctx.actor,
                tasks.TaskCreate(
                    project_id=project_id or self.project_id,
                    title=title,
                    label="ai",
                    status="today",
                    first_action="Open the footer component",
                    acceptance_criteria="The footer link opens the right page",
                ),
                now=self.clock.now(),
            )
        if tainted:
            await integrations.link_context(
                self.workspace.ctx,
                owner_type="task",
                owner_id=task.id,
                target_type="url",
                target_url=EMAIL_URL,
                added_by=self.workspace.ctx.actor,
            )
        return uuid.UUID(str(task.id))

    def packets(self, run_id: uuid.UUID) -> list[Json]:
        """The packets of the `run` messages the runner received for the run."""
        return [run.packet for run in self.runner.runs() if run.run_id == run_id]

    def token(self, run_id: uuid.UUID) -> str:
        """The task token the run's first packet carried (P2-02)."""
        packet = self.packets(run_id)[0]
        token: str = packet["callback"]["task_token"]
        return token

    async def delivered(
        self,
        run_id: uuid.UUID,
        timeout: float = 15,  # noqa: ASYNC109  # a polling deadline, not a cancel scope
    ) -> None:
        """Waits until the runner received the run's `run` message; the wait runs on a
        thread, so the test's loop keeps serving the relay."""
        await asyncio.to_thread(
            self.runner.wait_for, lambda r: any(m.run_id == run_id for m in r.runs()), timeout
        )

    def cancels(self, run_id: uuid.UUID) -> list[Any]:
        """The `cancel` messages the runner received for the run."""
        return [
            m
            for m in self.runner.received
            if getattr(m, "type", None) == "cancel" and getattr(m, "run_id", None) == run_id
        ]


async def _project_with_agent(
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    runner: FakeRunner,
    *,
    name: str,
    profile: str,
) -> tuple[uuid.UUID, uuid.UUID]:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415
    from tumnis.modules.auth import api as auth  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    ctx = user_ctx(workspace)
    async with tenant_session(ctx) as s:
        made = await projects.create_project(
            s, ctx.actor, projects.ProjectCreate(name=name), now=clock.now()
        )
    profile_id = fake_runner.register_profile(profile, runner=runner, project_id=made.id)
    key = await auth.create_key(
        workspace.ctx,
        auth.KeyIn(name=f"{profile} key", scopes=PROFILE_KEY_SCOPES),
        now=clock.now(),
    )
    await agents.set_profile_key(workspace.ctx, profile_id, key.id, now=clock.now())
    return made.id, profile_id


async def arrange_world(
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    *,
    master: bool = False,
) -> World:
    """`Acme site` with its agent `acme-site` on a protocol-2 fake runner and the key its
    runs issue task tokens from; `master` adds `tumnis-master` (role master) with a key of
    MASTER_KEY_SCOPES, kept as `world.master_key`."""
    from tumnis.modules.agents import api as agents  # noqa: PLC0415
    from tumnis.modules.auth import api as auth  # noqa: PLC0415

    names = [ACME_AGENT, MASTER] if master else [ACME_AGENT]
    runner = fake_runner(profiles=names, connect=False)
    runner.connect(protocol_versions=[1, 2], capabilities=V2_CAPABILITIES)
    project_id, profile_id = await _project_with_agent(
        fake_runner, workspace, clock, runner, name=ACME, profile=ACME_AGENT
    )
    world = World(workspace, clock, runner, project_id, profile_id, projects={ACME: project_id})
    if master:
        world.master_profile_id = fake_runner.register_profile(MASTER, runner=runner, role="master")
        key = await auth.create_key(
            workspace.ctx, auth.KeyIn(name="master key", scopes=MASTER_KEY_SCOPES), now=clock.now()
        )
        await agents.set_profile_key(
            workspace.ctx, world.master_profile_id, key.id, now=clock.now()
        )
        world.master_key = key.key
    return world


def human_wait_poll_seconds(seconds: int) -> None:
    """The long poll of `ask_human` and `request_approval` (plan default 600 s; the
    acceptance tests use 2 s), set the way P2-04's `configure_runs` sets its caps
    (fakes mode only)."""
    from tumnis.modules.agents import api as agents  # noqa: PLC0415

    configure = agents.configure_human_waits
    configure(poll_seconds=seconds)


# --- The outbox relay (the worker's job) ---------------------------------------------------


def _mark_outbox_sent(db: DbUrls) -> None:
    import psycopg  # noqa: PLC0415

    from tests._pg import OWNER  # noqa: PLC0415

    with psycopg.connect(db.libpq(OWNER)) as conn:
        conn.execute(b"UPDATE outbox SET sent_at = now() WHERE sent_at IS NULL")


@contextlib.asynccontextmanager
async def relay(db: DbUrls, *, poll_s: float = 0.1) -> AsyncIterator[None]:
    """The outbox relay on the test's loop, as the worker runs it (deliveries run on the
    `dbos` fixture). The set-up's own events are skipped (a `project.created` would
    provision a second profile)."""
    import tumnis.modules.agents.events  # noqa: PLC0415
    import tumnis.modules.tasks.events  # noqa: F401, PLC0415
    from tumnis.core import events  # noqa: PLC0415

    _mark_outbox_sent(db)
    stop = asyncio.Event()
    task = asyncio.create_task(events.relay_forever(stop, poll_s=poll_s))
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


async def until(
    check: Callable[[], Any],
    *,
    timeout: float = 15,  # noqa: ASYNC109  # a polling deadline, not a cancel scope
    every: float = 0.05,
) -> Any:
    """Polls `check` (sync or async) until it returns something truthy or `timeout`
    passes; returns the last value."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        value = check()
        if asyncio.iscoroutine(value):
            value = await value
        if value or loop.time() > deadline:
            return value
        await asyncio.sleep(every)


# --- Reads ---------------------------------------------------------------------------------


def rows(db: DbUrls, query: str, *params: Any) -> list[Json]:
    """Rows of a read as the owner (no row-level security), as dicts."""
    import psycopg  # noqa: PLC0415
    from psycopg.rows import dict_row  # noqa: PLC0415

    from tests._pg import OWNER  # noqa: PLC0415

    with psycopg.connect(db.libpq(OWNER), row_factory=dict_row) as conn:
        return list(conn.execute(query.encode(), params).fetchall())


def outbox(db: DbUrls, name: str, run_id: uuid.UUID | None = None) -> list[Json]:
    """Payloads of the outbox rows named `name` (only the run's, when given)."""
    found = [r["payload"] for r in rows(db, "SELECT payload FROM outbox WHERE name = %s", name)]
    if run_id is None:
        return found
    return [p for p in found if p.get("run_id") == str(run_id)]


def audit(db: DbUrls, action: str) -> list[Json]:
    """The audit rows of `action`, oldest first."""
    return rows(
        db,
        "SELECT actor_type, actor_id, action, target_type, target_id, reason, details "
        "FROM audit_log WHERE action = %s ORDER BY seq",
        action,
    )


async def get_json(http: httpx.AsyncClient, path: str, **params: Any) -> Any:
    """GET `path`; raises on an error status."""
    response = await http.get(path, params=params)
    response.raise_for_status()
    return response.json()


async def task_status(http: SessionClient, task_id: uuid.UUID) -> str:
    body = await get_json(http, f"/v1/tasks/{task_id}")
    return str(body["status"])


async def run_status(http: SessionClient, run_id: uuid.UUID) -> str:
    """`GET /v1/runs/{id}` (P2-04): the run's status."""
    body = await get_json(http, f"/v1/runs/{run_id}")
    return str(body["status"])


async def start_run(http: SessionClient, task_id: uuid.UUID) -> httpx.Response:
    """`POST /v1/tasks/{id}/run` as the user (the Run button)."""
    return await http.post(f"/v1/tasks/{task_id}/run", json={})


async def run_task(http: SessionClient, task_id: uuid.UUID) -> uuid.UUID:
    """Starts a run; raises on an error status; the run id."""
    response = await start_run(http, task_id)
    response.raise_for_status()
    return uuid.UUID(response.json()["run_id"])


async def review_items(http: SessionClient, kind: str) -> list[Json]:
    """`GET /v1/review?kind=`: the open items of one kind."""
    body = await get_json(http, "/v1/review", kind=kind, limit=100)
    items: list[Json] = body["items"] if isinstance(body, dict) else body
    return items


async def decide(
    http: SessionClient, item: Json, action: str, payload: Json | None = None
) -> httpx.Response:
    """`POST /v1/review/{id}/decide` (R-04) at the item's version."""
    body: Json = {"action": action, "version": item["version"]}
    if payload is not None:
        body["payload"] = payload
    return await http.post(f"/v1/review/{item['id']}/decide", json=body)


def wait_id(answer: Json) -> str:
    """The question or approval id a `HumanWaitOut` carries (`question_id` in the A2.2
    text, `id` in P2-05's model)."""
    return str(answer.get("question_id") or answer.get("approval_id") or answer.get("id"))


def result_summary(db: DbUrls, run_id: uuid.UUID) -> str | None:
    """The summary of the result posted for the run (P2-04's `results` table)."""
    found = rows(db, "SELECT summary FROM results WHERE run_id = %s", run_id)
    return None if not found else str(found[0]["summary"])


# --- The agent's side ----------------------------------------------------------------------


def _outcome(response: Any) -> Outcome:
    from tests._mcp import Outcome  # noqa: PLC0415

    try:
        body = response.json()
    except ValueError:
        return Outcome(response.status_code, f"http_{response.status_code}", response.text)
    if response.status_code != 200 or not isinstance(body, dict) or "result" not in body:
        code = body.get("code") if isinstance(body, dict) else None
        if isinstance(body, dict) and "error" in body:
            code = f"jsonrpc_{body['error'].get('code')}"
        return Outcome(response.status_code, code or f"http_{response.status_code}", body)
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


async def tool(runner: FakeRunner, token: str, name: str, args: Json) -> Outcome:
    """One MCP `tools/call` with the bearer `token` (a task token or a key), sent the way
    an agent sends it: a JSON-RPC POST to `/mcp` through the runner's TestClient, on a
    thread (a long poll must not hold the test's loop). A write op's arguments carry an
    `idempotency_key` (every MCP write needs one, P2-01) unless the caller gave one."""
    from tests._mcp import MCP_ACCEPT, MCP_PATH  # noqa: PLC0415
    from tumnis.core import agent_surface  # noqa: PLC0415
    from tumnis.wiring import load_mcp  # noqa: PLC0415

    load_mcp()
    if agent_surface.get_op(name).write and "idempotency_key" not in args:
        args = {**args, "idempotency_key": f"agent-{uuid.uuid4()}"}
    message = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/call",
        "params": {"name": name, "arguments": args},
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": MCP_ACCEPT,
        "Content-Type": "application/json",
        "Idempotency-Key": f"agent-{uuid.uuid4()}",
    }

    def send() -> Any:
        return runner.client.post(MCP_PATH, json=message, headers=headers)

    return _outcome(await asyncio.to_thread(send))


def stream(runner: FakeRunner, run_id: uuid.UUID, seq: int, text: str, kind: str = "log") -> None:
    """One protocol-2 `stream` line of the run."""
    frame = {
        "schema_version": 1,
        "type": "stream",
        "message_id": str(uuid.uuid4()),
        "correlation_id": f"run:{run_id}",
        "sent_at": T0,
        "run_id": str(run_id),
        "seq": seq,
        "kind": kind,
        "text": text,
        "ts": T0,
    }
    runner.send_raw(json.dumps(frame))


class Streamer:
    """A long-running agent: streams a log line every `every_s` seconds until the runner
    receives a `cancel` for the run (A2.5)."""

    def __init__(self, world: World, run_id: uuid.UUID, every_s: float = 0.2) -> None:
        self.world, self.run_id, self.every_s = world, run_id, every_s
        self.sent = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> Streamer:
        self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop.is_set() and not self.world.cancels(self.run_id):
            self.sent += 1
            try:
                stream(self.world.runner, self.run_id, self.sent, f"working ({self.sent})")
            except Exception:  # the socket went away
                return
            self._stop.wait(self.every_s)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(5)


RESULT_ARGS: Final[Json] = {
    "outcome": "done",
    "files_touched": [{"path": "src/footer.tsx", "change": "modified"}],
    "links": [],
}


async def post_result(runner: FakeRunner, token: str, run_id: uuid.UUID, summary: str) -> Outcome:
    """The agent's `post_result` over MCP (P2-04)."""
    return await tool(
        runner, token, "post_result", {"run_id": str(run_id), "summary": summary, **RESULT_ARGS}
    )
