"""Helpers for the P2-04 run tests (dispatch_run, results, run events).

No assertions live here: spec-guard locks the test bodies, and these helpers adapt to the
agents api, the fake runner and the outbox.

- `RunWorld` / `run_world(...)`: a project ("Acme site" by default) whose agent profile
  lives on a fake runner speaking protocol 2, optionally with an API key (so every
  dispatch carries a task token); `world.ai_task(...)` makes a task, `world.request(...)`
  asks for a run through `agents.api.request_run` as the workspace's user.
- `relay()`: the outbox relay running beside the test (the worker's job), after the
  set-up's own events are marked sent, so only what the test does is delivered. It runs
  on the test's event loop, which the fake runner's sync `wait_for` blocks: so while it
  runs, `world.request(...)` and `wait_until(...)` return only once the relay has claimed
  every event committed so far (`settle()`), and a `wait_for` that follows never waits
  on an event the blocked relay would have had to deliver. `settle()` also waits until
  the world's fake runner holds the `run` message of every running run of its profiles
  (a run is `running` from `prepare_run`, a moment before its packet reaches the runner).
  On exit it cancels the `dispatch_run` workflows of the runs still open (DBOS state only),
  so teardown does not wait for a workflow parked in `recv`.
- `finish(runner, run_id, output)`, `stream(runner, run_id, seq, text)`: what a daemon
  sends for a run (protocol-2 frames), with their message ids.
- `wait_until(check)`, `owner_rows(db, sql, params)`, `workflow_status(run_id)`.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import itertools
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

import psycopg

from tests._pg import OWNER

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunner, FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.core.tenancy import WorkspaceContext

T: Final = "2026-03-09T12:00:00Z"
V2_CAPABILITIES: Final = ["run", "health", "stream", "cancel", "upload_artifact", "worktree"]
WORK_SKILL: Final = "work"
KEY_SCOPES: Final = ["tasks:read", "tasks:write", "context:read"]
RESULT_OUTPUT: Final[dict[str, Any]] = {
    "outcome": "done",
    "summary": "Fixed the footer link",
    "files_touched": [{"path": "src/footer.tsx", "change": "modified"}],
    "links": [
        {"kind": "branch", "url": "https://github.com/acme/site/tree/fix-footer", "label": "fix"},
        {"kind": "pull_request", "url": "https://github.com/acme/site/pull/7", "label": "PR"},
    ],
}
_ids = itertools.count(1)
_relaying: list[DbUrls] = []  # the database of the relay running beside the test, if any
_runner: list[FakeRunner] = []  # the fake runner of the latest run_world
SETTLE_S: Final = 15.0


def owner_rows(db: DbUrls, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Rows read as the owner role (row-level security does not apply)."""
    with psycopg.connect(db.libpq(OWNER)) as conn:
        return conn.execute(query.encode(), params).fetchall()


def mark_outbox_sent(db: DbUrls) -> None:
    """Mark every outbox row sent: the set-up's events (project.created would provision a
    second profile) are never delivered."""
    with psycopg.connect(db.libpq(OWNER)) as conn:
        conn.execute(b"UPDATE outbox SET sent_at = now() WHERE sent_at IS NULL")


async def settle(*, every: float = 0.05) -> None:
    """While `relay()` runs: wait (at most SETTLE_S) until it has claimed every outbox
    row committed so far, so their deliveries are queued before the test blocks its event
    loop. No relay beside the test (a worker relays): nothing to wait for."""
    if not _relaying:
        return
    db = _relaying[-1]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SETTLE_S
    while not _settled(db):
        if loop.time() > deadline:
            return
        await asyncio.sleep(every)


def _settled(db: DbUrls) -> bool:
    """No outbox row waits for the relay, and the fake runner holds every running run of
    its profiles."""
    if owner_rows(db, "SELECT count(*) FROM outbox WHERE sent_at IS NULL") != [(0,)]:
        return False
    if not _runner:
        return True
    runner = _runner[-1]
    active = owner_rows(
        db,
        "SELECT r.id FROM runs r JOIN agent_profiles p ON p.id = r.profile_id"
        " WHERE r.status IN ('running', 'waiting_on_human') AND p.runner_id = %s",
        (runner.runner_id,),
    )
    received = {run.run_id for run in runner.runs()}
    return all(run_id in received for (run_id,) in active)


async def wait_until(
    check: Callable[[], Awaitable[bool]] | Callable[[], bool],
    *,
    timeout: float = 15,  # noqa: ASYNC109  # a polling deadline, not a cancel scope
    every: float = 0.05,
) -> bool:
    """Poll `check` until it is true (then `settle()`); False when `timeout` passed
    first."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        found = check()
        if asyncio.iscoroutine(found):
            found = await found
        if found:
            await settle(every=every)
            return True
        if loop.time() > deadline:
            return False
        await asyncio.sleep(every)


@contextlib.asynccontextmanager
async def relay(db: DbUrls, *, poll_s: float = 0.1) -> AsyncIterator[None]:
    """The outbox relay on this test's event loop, as the worker runs it; its deliveries
    run on the `dbos` fixture's executor. Events already in the outbox are skipped."""
    import tumnis.wiring  # noqa: F401, PLC0415  # registers every module's subscribers
    from tumnis.core import events  # noqa: PLC0415

    mark_outbox_sent(db)
    stop = asyncio.Event()
    task = asyncio.create_task(events.relay_forever(stop, poll_s=poll_s))
    _relaying.append(db)
    try:
        yield
    finally:
        _relaying.remove(db)
        stop.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        await _cancel_open_dispatches(db)


async def _cancel_open_dispatches(db: DbUrls) -> None:
    """Cancel the `dispatch_run` workflows of the runs the test left open, so the `dbos`
    fixture's teardown does not wait out its 10 s for a workflow parked in `recv`. Only
    DBOS state changes (no run, event or outbox row). DBOS 3.1.0 checks cancellation at a
    workflow's next step, and `recv` does not wake for it: an empty message on the run's
    topic wakes it, and its next step (`now_s`) aborts."""
    from dbos import DBOS  # noqa: PLC0415

    from tumnis.modules.agents import api  # noqa: PLC0415

    open_runs = owner_rows(
        db,
        "SELECT id, workflow_id FROM runs"
        " WHERE status IN ('queued', 'running', 'waiting_on_human')",
    )
    for run_id, workflow_id in open_runs:
        for target in {str(run_id), workflow_id or str(run_id)}:
            with contextlib.suppress(Exception):
                await DBOS.cancel_workflow_async(target)
                await DBOS.send_async(target, {}, topic=api.run_topic(run_id))
    # P2-05: the question and approval flows parked on the human (topic `human`).
    waits = owner_rows(
        db,
        "SELECT workflow_id FROM questions WHERE workflow_id IS NOT NULL"
        " UNION ALL SELECT workflow_id FROM approvals WHERE workflow_id IS NOT NULL",
    )
    for (workflow_id,) in waits:
        with contextlib.suppress(Exception):
            await DBOS.cancel_workflow_async(workflow_id)
            await DBOS.send_async(workflow_id, {}, topic=api.HUMAN_TOPIC)


def user_ctx(workspace: WorkspaceHandle) -> WorkspaceContext:
    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.core.types import ActorRef  # noqa: PLC0415

    return WorkspaceContext(workspace.id, ActorRef(f"user:{workspace.user_id}"))


def workflow_status(run_id: uuid.UUID) -> str | None:
    """The DBOS status of the run's `dispatch_run` workflow (its id is the run id). Read on
    a plain thread: DBOS refuses its sync API while an event loop runs on the caller's."""
    from dbos import DBOS  # noqa: PLC0415

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        status = pool.submit(DBOS.get_workflow_status, str(run_id)).result()
    return None if status is None else str(status.status)


@dataclass
class RunWorld:
    workspace: WorkspaceHandle
    clock: FixedClock
    runner: FakeRunner
    profile_id: uuid.UUID
    project_id: uuid.UUID
    profile: str
    projects: dict[str, uuid.UUID] = field(default_factory=dict)

    @property
    def ctx(self) -> WorkspaceContext:
        return user_ctx(self.workspace)

    async def ai_task(
        self, title: str | None = None, *, label: str = "ai", project_id: uuid.UUID | None = None
    ) -> Any:
        """A fresh root task, made by the workspace's user."""
        from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
        from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

        async with tenant_session(self.ctx) as s:
            return await tasks.create_task(
                s,
                self.ctx.actor,
                tasks.TaskCreate(
                    project_id=project_id or self.project_id,
                    title=title or f"Run task {next(_ids)}",
                    label=label,
                    estimate_minutes=30 if label != "ai" else None,
                    acceptance_criteria="The footer link works",
                ),
                now=self.clock.now(),
            )

    async def request(self, task_id: uuid.UUID, kind: str = "task") -> uuid.UUID:
        from tumnis.modules.agents import api as agents  # noqa: PLC0415

        run_id: uuid.UUID = await agents.request_run(task_id, agents.RunKind(kind), ctx=self.ctx)
        await settle()  # run.requested claimed before the test may block its loop
        return run_id

    def packets(self, run_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
        """The packets of the `run` messages the runner received (for one run, or all)."""
        return [run.packet for run in self.runner.runs() if run_id is None or run.run_id == run_id]

    def token(self, run_id: uuid.UUID) -> str:
        """The task token the run's packet carried."""
        [packet] = self.packets(run_id)[:1]
        token: str = packet["callback"]["task_token"]
        return token


async def add_project_agent(
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    runner: FakeRunner,
    *,
    project_name: str,
    profile: str,
    key: bool,
) -> tuple[uuid.UUID, uuid.UUID]:
    """A project and its agent profile on `runner`; (project id, profile id)."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415
    from tumnis.modules.auth import api as auth  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    ctx = user_ctx(workspace)
    async with tenant_session(ctx) as s:
        made = await projects.create_project(
            s, ctx.actor, projects.ProjectCreate(name=project_name), now=clock.now()
        )
    profile_id = fake_runner.register_profile(profile, runner=runner, project_id=made.id)
    if key:
        created = await auth.create_key(
            workspace.ctx, auth.KeyIn(name=f"{profile} key", scopes=KEY_SCOPES), now=clock.now()
        )
        await agents.set_profile_key(workspace.ctx, profile_id, created.id, now=clock.now())
    return made.id, profile_id


async def run_world(
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    *,
    project_name: str = "Acme site",
    profile: str = "acme-site",
    others: tuple[tuple[str, str], ...] = (),
    key: bool = True,
) -> RunWorld:
    """A fake runner on protocol 2 with the project's profile (and `others`: more
    (project name, profile) pairs on the same runner)."""
    names = [profile, *(p for _, p in others)]
    runner = fake_runner(profiles=names, connect=False)
    runner.connect(protocol_versions=[1, 2], capabilities=V2_CAPABILITIES)
    project_id, profile_id = await add_project_agent(
        fake_runner, workspace, clock, runner, project_name=project_name, profile=profile, key=key
    )
    world = RunWorld(workspace, clock, runner, profile_id, project_id, profile)
    _runner[:] = [runner]
    world.projects[project_name] = project_id
    for other_project, other_profile in others:
        other_id, _ = await add_project_agent(
            fake_runner,
            workspace,
            clock,
            runner,
            project_name=other_project,
            profile=other_profile,
            key=key,
        )
        world.projects[other_project] = other_id
    return world


def _frame(type_: str, run_id: uuid.UUID, **fields: Any) -> dict[str, Any]:
    return {
        "message_id": str(uuid.uuid4()),
        "correlation_id": f"run:{run_id}",
        "sent_at": T,
        "type": type_,
        "run_id": str(run_id),
        **fields,
    }


def finish(
    runner: FakeRunner,
    run_id: uuid.UUID,
    output: dict[str, Any] | None = None,
    *,
    status: str = "succeeded",
) -> uuid.UUID:
    """The daemon's `result` for the run (protocol 2); its message id."""
    frame = _frame(
        "result",
        run_id,
        schema_version=2,
        status=status,
        exit_code=0 if status == "succeeded" else 1,
        output_json=RESULT_OUTPUT if output is None and status == "succeeded" else output,
        text="done" if status == "succeeded" else "",
        error=None if status == "succeeded" else f"scripted {status}",
        duration_ms=10,
    )
    runner.send_raw(json.dumps(frame))
    return uuid.UUID(frame["message_id"])


def stream(
    runner: FakeRunner,
    run_id: uuid.UUID,
    seq: int,
    text: str,
    *,
    kind: str = "log",
    message_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """One `stream` line of the run (protocol 2); a given `message_id` replays a line."""
    frame = _frame("stream", run_id, schema_version=1, seq=seq, kind=kind, text=text, ts=T)
    if message_id is not None:
        frame["message_id"] = str(message_id)
    runner.send_raw(json.dumps(frame))
    return uuid.UUID(frame["message_id"])


def cancels(runner: FakeRunner, run_id: uuid.UUID) -> list[Any]:
    """The `cancel` messages the runner received for the run."""
    return [
        m
        for m in runner.received
        if getattr(m, "type", None) == "cancel" and getattr(m, "run_id", None) == run_id
    ]
