"""Helpers for the push integration tests (P4-05). No assertions: they make rows through the
modules' apis and the app's routes, let the in-process worker settle, and read rows as the
owner, so the code under test may change without touching a locked test body.

- `PushWorld` (the `push` fixture): `subscribe()` (a browser subscription through
  `POST /v1/push/subscriptions`, its keys generated here), `level(level)`, `task(title)`,
  `move(task, *path)`, `review_item(task)` (an `estimate_outlier` item through tasks' api),
  `focus_event(kind, task)` (a `focus.event` as P2-15 emits it), `emit(name, **payload)`,
  `sent` (what the fake push service got), `pushed(tag)`, `attempts()`,
  `subscriptions()` and `settle()`.
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import os
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import psycopg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from psycopg.rows import dict_row

from tests._pg import APP, OWNER

if TYPE_CHECKING:
    import httpx

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

SETTLE_S = 30.0
QUIET_S = 0.3
# P2-16 adds Discord delivery: its workflow and the master's notify runs.
WORKFLOWS = (
    "deliver_event",
    "notifications.deliver_push",
    "notifications.deliver_notification",
    "run_skill",
)

# Every module's workflows and subscribers, registered before DBOS launches.
importlib.import_module("tumnis.wiring")


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def rows(db: DbUrls, query: str, *params: Any) -> list[dict[str, Any]]:
    with psycopg.connect(db.libpq(OWNER), row_factory=dict_row) as conn:
        return list(conn.execute(query.encode(), params).fetchall())


def execute(db: DbUrls, query: str, *params: Any) -> None:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        conn.execute(query.encode(), params)


def browser_keys() -> dict[str, str]:
    """A browser subscription's two keys, generated now (W3C Push API, RFC 8291)."""
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return {"p256dh": b64url(public), "auth": b64url(os.urandom(16))}


class PushWorld:
    """One workspace's push world: its user signed in on the in-process app, the server
    clock, the in-process worker and the fake push service every delivery reaches."""

    def __init__(  # noqa: PLR0917  # the fixtures it stands on
        self,
        workspace: WorkspaceHandle,
        clock: FixedClock,
        db: DbUrls,
        sys_db: DbUrls,
        http: httpx.AsyncClient,
        fake: Any,
    ) -> None:
        self.workspace = workspace
        self.clock = clock
        self.db = db
        self.sys_db = sys_db
        self.http = http
        self.fake = fake
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
        """Mark every outbox row sent: the set-up's events are never delivered."""
        execute(self.db, "UPDATE outbox SET sent_at = now() WHERE sent_at IS NULL")

    async def subscribe(self, endpoint: str | None = None) -> httpx.Response:
        """`POST /v1/push/subscriptions` with a fresh browser subscription."""
        body = {
            "endpoint": endpoint or f"https://fcm.googleapis.com/fcm/send/{uuid4()}",
            "keys": browser_keys(),
        }
        return await self.http.post("/v1/push/subscriptions", json=body)

    async def project(self, name: str = "Acme site") -> UUID:
        from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
        from tumnis.modules.projects import api as projects  # noqa: PLC0415

        ctx = self.ctx()
        async with tenant_session(ctx) as s:
            made = await projects.create_project(
                s, ctx.actor, projects.ProjectCreate(name=name), now=self.now
            )
        self.forget_setup()
        return made.id

    async def task(self, title: str) -> UUID:
        from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
        from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

        if self._project is None:
            self._project = await self.project()
        ctx = self.ctx()
        async with tenant_session(ctx) as s:
            made = await tasks.create_task(
                s,
                ctx.actor,
                tasks.TaskCreate(project_id=self._project, title=title, label="human"),
                now=self.now,
            )
        self.forget_setup()
        return UUID(str(made.id))

    async def level(self, level: str) -> httpx.Response:
        """`PUT /v1/focus/level`, then settle."""
        answer = await self.http.put("/v1/focus/level", json={"level": level})
        await self.settle()
        return answer

    async def move(self, task_id: UUID, *path: str) -> None:
        """Walk the task through these statuses as the user, a minute apart, settling after
        each."""
        from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
        from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

        for to in path:
            self.now += timedelta(minutes=1)
            ctx = self.ctx()
            async with tenant_session(ctx) as s:
                task = await tasks.get_task(s, task_id)
                await tasks.change_status(
                    s, ctx.actor, task_id, tasks.Status(to), task.version, now=self.now
                )
            await self.settle()

    async def review_item(self, task_id: UUID) -> UUID:
        """An `estimate_outlier` review item on the task (P1-08's kind), then settle."""
        from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
        from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

        async with tenant_session(self.ctx("system")) as s:
            item = await tasks.add_review_item(
                "estimate_outlier",
                target=tasks.TargetRef(type="task", id=task_id),
                project_id=self._project,
                payload={"flag": "too_high", "estimate_minutes": 900},
                session=s,
            )
        await self.settle()
        return item

    async def emit(self, name: str, **payload: Any) -> None:
        """Emit event `name` v1 in the workspace as the system would, then settle."""
        from tumnis.core.events import registry  # noqa: PLC0415
        from tumnis.core.outbox import emit  # noqa: PLC0415
        from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

        model = registry.model(name, 1)
        self.now += timedelta(minutes=1)
        async with tenant_session(self.ctx("system")) as s:
            await emit(s, model.model_validate(payload), occurred_at=self.now)
        await self.settle()

    async def focus_event(self, kind: str, task_id: UUID | None, level: str) -> UUID:
        """A `focus.event` of `kind` as P2-15 emits it (it fired at `level`); its id."""
        event_id = uuid4()
        await self.emit(
            "focus.event",
            event_id=event_id,
            kind=kind,
            task_id=task_id,
            rule=f"{level}:{kind}",
            level=level,
            message="Time to start: Write proposal",
            fired_at=self.now + timedelta(minutes=1),
        )
        return event_id

    # --- reading -----------------------------------------------------------------------

    @property
    def sent(self) -> list[Any]:
        """(subscription, payload, ttl) for every push the fake service took, in order."""
        return list(self.fake.sent)

    def pushed(self, tag: str | UUID) -> list[Any]:
        """The payloads pushed with this tag."""
        return [payload for _sub, payload, _ttl in self.fake.sent if payload.tag == str(tag)]

    def attempts(self) -> list[dict[str, Any]]:
        return rows(
            self.db,
            "SELECT * FROM delivery_attempts WHERE workspace_id = %s ORDER BY created_at, id",
            self.workspace.id,
        )

    def subscriptions(self) -> list[dict[str, Any]]:
        return rows(
            self.db,
            "SELECT * FROM push_subscriptions WHERE workspace_id = %s AND deleted_at IS NULL"
            " ORDER BY created_at, id",
            self.workspace.id,
        )

    # --- settling ----------------------------------------------------------------------

    def _pending(self) -> int:
        with psycopg.connect(self.sys_db.libpq(APP)) as conn:
            row = conn.execute(
                b"SELECT count(*) FROM dbos.workflow_status"
                b" WHERE name = ANY(%s) AND status IN ('ENQUEUED', 'PENDING')",
                (list(WORKFLOWS),),
            ).fetchone()
        return int(row[0]) if row else 0

    def _fingerprint(self) -> tuple[int, int]:
        [row] = rows(self.db, "SELECT count(*) AS o FROM outbox WHERE sent_at IS NULL")
        return int(row["o"]), len(self.fake.sent)

    async def settle(self) -> None:
        """Relay the outbox until every delivery and push workflow has run and nothing has
        changed for a moment."""
        from tumnis.core.events import relay_once  # noqa: PLC0415

        loop = asyncio.get_running_loop()
        deadline = loop.time() + SETTLE_S
        calm, last = 0, None
        while calm < 3:  # three quiet looks in a row
            sent = await relay_once()
            seen = self._fingerprint()
            quiet = not sent and self._pending() == 0 and seen == last
            calm = calm + 1 if quiet else 0
            last = seen
            if loop.time() > deadline:
                return
            await asyncio.sleep(QUIET_S / 3)
