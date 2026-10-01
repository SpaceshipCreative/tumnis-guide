"""Helpers for the focus integration tests (P2-15). No assertions: they make rows through the
modules' apis, drive the server clock and the `focus-wake` tick through the test routes,
let the in-process worker settle, and read rows as the owner, so the code under test may
change without touching a locked test body.

- `at(day, "hh:mm")`: a New York wall time (the `workspace` fixture's zone) in UTC.
- `Focus` (the `focus` fixture): `task(...)`, `publish(day, blocks)`, `level(level)`,
  `advance(to)` (server clock to `to`, one `focus-wake` tick, settle), `move(task, *path)`,
  `respond(kind, response)`, `less()`, `current()`, `events(kind)`, `payloads(name)`,
  `sessions()`, `settle()` and `wake_through(client, now)` (a tick sent through another
  process's DBOS client: the kill test's worker).
- `nudge_noul(p)` / `decisions_down()`: fake Decisions providers for the Noul gate.
- `until(check)`: polls until `check` returns something truthy.
"""

from __future__ import annotations

import asyncio
import importlib
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from datetime import time as wall_time
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from tests._pg import APP, OWNER

if TYPE_CHECKING:
    import httpx

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

NEW_YORK = ZoneInfo("America/New_York")
TUESDAY = date(2026, 3, 10)
WEDNESDAY = date(2026, 3, 11)
SETTLE_S = 30.0
QUIET_S = 0.3
AGENT = "api_key:0199aa00-0000-7000-8000-00000000f0c7"  # an agent's key: In progress -> In review

# Every module's workflows and subscribers, registered before DBOS launches.
importlib.import_module("tumnis.wiring")


def at(day: date, hhmm: str) -> datetime:
    """`hh:mm` New York time on `day`, in UTC."""
    hours, minutes = (int(x) for x in hhmm.split(":"))
    return datetime.combine(day, wall_time(hours, minutes), tzinfo=NEW_YORK).astimezone(UTC)


def rows(db: DbUrls, query: str, *params: Any) -> list[dict[str, Any]]:
    with psycopg.connect(db.libpq(OWNER), row_factory=dict_row) as conn:
        return list(conn.execute(query.encode(), params).fetchall())


def execute(db: DbUrls, query: str, *params: Any) -> None:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        conn.execute(query.encode(), params)


async def until(
    check: Callable[[], Any], *, timeout_s: float = SETTLE_S, poll_s: float = 0.1
) -> Any:
    """The first truthy value of `check`, or its last value once `timeout_s` has passed."""
    deadline = time.monotonic() + timeout_s
    while True:
        value = check()
        if asyncio.iscoroutine(value) or isinstance(value, Awaitable):
            value = await value
        if value or time.monotonic() >= deadline:
            return value
        await asyncio.sleep(poll_s)


class Focus:
    """One workspace's focus world: its user signed in on the in-process app, the server
    clock, and (when the test also asks for `dbos`) the in-process worker."""

    def __init__(  # the fixtures it stands on
        self,
        workspace: WorkspaceHandle,
        clock: FixedClock,
        db: DbUrls,
        sys_db: DbUrls,
        http: httpx.AsyncClient,
        *,
        in_process: bool,
    ) -> None:
        self.workspace = workspace
        self.clock = clock
        self.db = db
        self.sys_db = sys_db
        self.http = http
        self.in_process = in_process
        self.now = clock.now()
        self._project: UUID | None = None

    # --- arranging ---------------------------------------------------------------------

    def ctx(self, actor: str | None = None) -> Any:
        from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
        from tumnis.core.types import ActorRef  # noqa: PLC0415

        return WorkspaceContext(
            self.workspace.id, ActorRef(actor or f"user:{self.workspace.user_id}")
        )

    def forget_setup(self) -> None:
        """Mark every outbox row sent: the set-up's events (project.created would provision
        an agent) are never delivered."""
        execute(self.db, "UPDATE outbox SET sent_at = now() WHERE sent_at IS NULL")

    async def project(self, name: str = "Acme site", cadence_min: int | None = None) -> UUID:
        from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
        from tumnis.modules.projects import api as projects  # noqa: PLC0415

        ctx = self.ctx()
        async with tenant_session(ctx) as s:
            made = await projects.create_project(
                s, ctx.actor, projects.ProjectCreate(name=name), now=self.now
            )
        if cadence_min is not None:
            execute(
                self.db,
                "UPDATE projects SET focus_cadence_min = %s WHERE id = %s",
                cadence_min,
                made.id,
            )
        self.forget_setup()
        return made.id

    async def task(
        self,
        title: str,
        *,
        first_action: str | None = None,
        project: UUID | None = None,
    ) -> UUID:
        """A Human task (with this first action) in `project`, or in the world's project."""
        from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
        from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

        if project is None:
            if self._project is None:
                self._project = await self.project()
            project = self._project
        ctx = self.ctx()
        async with tenant_session(ctx) as s:
            made = await tasks.create_task(
                s,
                ctx.actor,
                tasks.TaskCreate(
                    project_id=project, title=title, label="human", first_action=first_action
                ),
                now=self.now,
            )
        self.forget_setup()
        return UUID(str(made.id))

    async def publish(
        self, day: date, blocks: list[tuple[UUID, str, str]], *, built: str = "08:30"
    ) -> UUID:
        """Publish the day's plan: these tasks in order, each blocked from start to end
        (New York time), built at `built`; `plan.published` is emitted then."""
        from tumnis.core.types import Interval  # noqa: PLC0415
        from tumnis.modules.planning import api as planning  # noqa: PLC0415

        draft = planning.PlanDraft.model_validate(
            {
                "plan_id": uuid4(),
                "day": day,
                "trigger": "replan",
                "source": "manual",
                "notice": None,
                "fallback_reason": None,
                "master_run_id": None,
                "profile_version": None,
                "built_at": at(day, built),
                "items": [
                    {
                        "task_id": task_id,
                        "position": i,
                        "reason": "Planned for focus",
                        "block": Interval(at(day, start), at(day, end)),
                    }
                    for i, (task_id, start, end) in enumerate(blocks)
                ],
                "issues": [],
            }
        )
        plan_id = await planning.publish_plan(self.ctx("system"), draft)
        await self.settle()
        return plan_id

    # --- the product -------------------------------------------------------------------

    async def level(self, level: str) -> httpx.Response:
        """`PUT /v1/focus/level`: the workspace's focus level."""
        answer = await self.http.put("/v1/focus/level", json={"level": level})
        await self.settle()
        return answer

    async def set_clock(self, to: datetime) -> None:
        await self.http.post("/v1/test/clock", json={"time": to.isoformat()})
        self.now = to

    async def advance(self, to: datetime) -> None:
        """The server clock to `to`, one `focus-wake` tick, then settle."""
        await self.set_clock(to)
        await self.http.post("/v1/test/tick/focus-wake")
        await self.settle()

    async def wake_through(self, client: Any, now: datetime) -> None:
        """One `focus-wake` tick at `now` sent through `client` (another process's DBOS)."""
        from tumnis.modules.focus import testing  # noqa: PLC0415

        await testing.wake(client, now)

    async def move(self, task_id: UUID, *path: str, at_time: datetime | None = None) -> None:
        """Walk the task through these statuses at the server's time (or `at_time`), as
        the user (an agent's key for In progress -> In review)."""
        from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
        from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

        now = at_time or self.now
        for to in path:
            actor = AGENT if to == "in_review" else None
            ctx = self.ctx(actor)
            async with tenant_session(ctx) as s:
                task = await tasks.get_task(s, task_id)
                await tasks.change_status(
                    s, ctx.actor, task_id, tasks.Status(to), task.version, now=now
                )
        await self.settle()

    def latest(self, kind: str) -> dict[str, Any] | None:
        found = self.events(kind)
        return found[-1] if found else None

    async def respond(self, kind: str, response: str, **extra: Any) -> httpx.Response:
        """`POST /v1/focus/respond` to the latest event of `kind`."""
        event = self.latest(kind)
        body = {"event_id": str(event["id"]) if event else str(uuid4()), "response": response}
        answer = await self.http.post("/v1/focus/respond", json=body | extra)
        await self.settle()
        return answer

    async def less(self, kind: str | None = None) -> httpx.Response:
        """`POST /v1/focus/less`, on the latest event of `kind` when given."""
        event = self.latest(kind) if kind else None
        body = {"event_id": str(event["id"])} if event else {}
        answer = await self.http.post("/v1/focus/less", json=body)
        await self.settle()
        return answer

    async def current(self) -> dict[str, Any]:
        answer = await self.http.get("/v1/focus/current")
        return dict(answer.json())

    def short_waits(self, seconds: float) -> None:
        """Cut every session's wait for its next check-in to at most `seconds` of real time
        (R-30): a wait that times out counts as the check-in coming due."""
        from tumnis.modules.focus import workflows  # noqa: PLC0415

        workflows.use(min_due_seconds=seconds)

    def use_decisions(self, provider: Any) -> None:
        """The decisions provider every `decide` in this process asks (None: the real
        chain)."""
        from tumnis.modules.decisions import api as decisions  # noqa: PLC0415

        decisions.use_providers(
            None if provider is None else decisions.Providers(jev=provider, vllm=None)
        )

    def workflow_status(self, workflow_id: str) -> str | None:
        with psycopg.connect(self.sys_db.libpq(APP)) as conn:
            row = conn.execute(
                b"SELECT status FROM dbos.workflow_status WHERE workflow_uuid = %s",
                (workflow_id,),
            ).fetchone()
        return None if row is None else str(row[0])

    # --- reading -----------------------------------------------------------------------

    def events(self, kind: str | None = None) -> list[dict[str, Any]]:
        """The workspace's focus_events rows, oldest first (of `kind` when given)."""
        found = rows(
            self.db,
            "SELECT * FROM focus_events WHERE workspace_id = %s ORDER BY fired_at, created_at",
            self.workspace.id,
        )
        return [e for e in found if kind is None or e["kind"] == kind]

    def kinds(self) -> list[tuple[str, datetime]]:
        return [(e["kind"], e["fired_at"]) for e in self.events()]

    def payloads(self, name: str) -> list[dict[str, Any]]:
        """The payloads of the workspace's outbox events named `name`, in order."""
        found = rows(
            self.db,
            "SELECT payload FROM outbox WHERE name = %s AND workspace_id = %s ORDER BY id",
            name,
            self.workspace.id,
        )
        return [dict(r["payload"]) for r in found]

    def sessions(self) -> list[dict[str, Any]]:
        return rows(
            self.db,
            "SELECT * FROM focus_sessions WHERE workspace_id = %s ORDER BY started_at",
            self.workspace.id,
        )

    # --- settling ----------------------------------------------------------------------

    def _pending_messages(self) -> int:
        with psycopg.connect(self.sys_db.libpq(APP)) as conn:
            row = conn.execute(
                b"SELECT count(*) FROM dbos.notifications WHERE topic = 'focus'"
            ).fetchone()
        return int(row[0]) if row else 0

    def _fingerprint(self) -> tuple[int, int, int]:
        [row] = rows(
            self.db,
            "SELECT (SELECT count(*) FROM focus_events) AS e,"
            " (SELECT count(*) FROM focus_sessions) AS s,"
            " (SELECT count(*) FROM outbox) AS o",
        )
        return int(row["e"]), int(row["s"]), int(row["o"])

    async def settle(self) -> None:
        """In-process worker only: relay the outbox until every delivery has run and every
        focus message is consumed, and nothing has changed for a moment."""
        if not self.in_process:
            return
        from dbos import DBOS  # noqa: PLC0415

        from tumnis.core.events import relay_once  # noqa: PLC0415

        loop = asyncio.get_running_loop()
        deadline = loop.time() + SETTLE_S
        calm, last = 0, None
        while calm < 3:  # three quiet looks in a row
            sent = await relay_once()
            busy = await DBOS.list_workflows_async(
                status=["ENQUEUED", "PENDING"], name="deliver_event", load_input=False
            )
            seen = self._fingerprint()
            quiet = not sent and not busy and self._pending_messages() == 0 and seen == last
            calm = calm + 1 if quiet else 0
            last = seen
            if loop.time() > deadline:
                return
            await asyncio.sleep(QUIET_S / 3)


def _fake_decisions() -> Any:
    """A fresh decisions fake, loaded by name (test scaffolding, never imported by focus)."""
    return importlib.import_module("tumnis.modules.decisions.adapters.fake").FakeDecisions()


def nudge_noul(p: float) -> Any:
    """A fake Decisions provider whose `nudge_warranted` answers "a nudge is warranted"
    with probability `p`."""
    fake = _fake_decisions()
    fake.script("nudge_warranted", {"nudge": {"type": "noul", "noul": p}})
    return fake


def decisions_down() -> Any:
    """A fake Decisions provider that fails as unavailable (Decisions down)."""
    from tumnis.core.adapters.errors import AdapterUnavailable  # noqa: PLC0415

    fake = _fake_decisions()
    fake.script("nudge_warranted", fail=AdapterUnavailable("decisions.fake", "ask", "down"))
    return fake
