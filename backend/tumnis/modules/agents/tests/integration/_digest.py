"""Helpers for the digest spec tests (P2-03). No assertions live here: spec-guard locks the
test bodies, and this module is where later work packages plug in their shapes.

Every source reaches the digest the way production feeds it: an outbox event handed to the
agents subscribers. Where the emitting WP has not merged yet, the helper makes the same
event itself; when it lands, only the helper changes.

- `digest_world(workspace, clock)`: projects P and Q made through the projects api, and the
  user's context.
- `decide(...)`: a `human.decided` event (R-07's payload) written to the outbox, as review
  decisions write it (P1-07 label overrides, P1-13 review, P2-04 results, P2-05 approvals
  and questions, P3-07 proposals). The payload model comes from the core event registry,
  since agents' tests may import no other module's payloads.
- `deliver(workspace_id, name, payload, at)`: an event whose payload model belongs to a WP
  still in flight (`document.added`/`document.changed`: P1-16; `focus.level_changed`:
  P2-15) handed straight to the agents subscribers, as the relay would.
- `finish_hybrid(world, title)`: a Hybrid task started, then done 75 minutes later.
- `comment(world, task_id, text, actor)`: `tasks.api.add_comment`.
- `link_email(world, task_id, subject, body)`: an email ingested through integrations'
  `ingest_page` (a small connector defined here) and linked to the task.
- `drain(db)`: the relay run until the outbox is empty, then every agents delivery of the
  rows it sent awaited.
- `digest_subscriber(event)`: the agents subscriber of an event.
- `DigestConsumer`: reads a digest like the digest skill does: passes the previous
  `next_cursor` as `since` (which acknowledges it) and pages while `has_more`.
- `blocks(text)`: the untrusted blocks in a text, as (open tag attributes, raw content).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Final

import psycopg
from psycopg.rows import dict_row

from tests._pg import OWNER

if TYPE_CHECKING:
    import httpx
    from dbos import WorkflowHandleAsync

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.core.events import Subscriber
    from tumnis.core.tenancy import WorkspaceContext

DIGEST_PATHS: Final = {
    "project": "/v1/digests/project/{project_id}",
    "workspace": "/v1/digests/workspace",
}
_BLOCK = re.compile(
    r'<untrusted-data (?P<attrs>id="(?P<id>u-[0-9a-f]+)"[^>]*)>\n(?P<body>.*?)\n'
    r'</untrusted-data id="(?P=id)">',
    re.DOTALL,
)
_n = iter(range(1, 1_000_000))


@dataclass
class DigestWorld:
    workspace: WorkspaceHandle
    clock: FixedClock
    projects: dict[str, uuid.UUID] = field(default_factory=dict)

    @property
    def ctx(self) -> WorkspaceContext:
        from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
        from tumnis.core.types import ActorRef  # noqa: PLC0415

        return WorkspaceContext(self.workspace.id, ActorRef(f"user:{self.workspace.user_id}"))

    async def task(self, project: str, **overrides: Any) -> Any:
        from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
        from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

        overrides.setdefault("title", f"Digest task {next(_n)}")
        overrides.setdefault("label", "human")
        overrides.setdefault("estimate_minutes", 60)
        async with tenant_session(self.ctx) as s:
            return await tasks.create_task(
                s,
                self.ctx.actor,
                tasks.TaskCreate(project_id=self.projects[project], **overrides),
                now=self.clock.now(),
            )


async def digest_world(workspace: WorkspaceHandle, clock: FixedClock) -> DigestWorld:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    world = DigestWorld(workspace, clock)
    for name in ("P", "Q"):
        async with tenant_session(world.ctx) as s:
            made = await projects.create_project(
                s,
                world.ctx.actor,
                projects.ProjectCreate(name=f"Digest {name} {uuid.uuid4().hex[:6]}"),
                now=clock.now(),
            )
        world.projects[name] = made.id
    return world


async def decide(  # noqa: PLR0917
    world: DigestWorld,
    item_kind: str,
    task_id: uuid.UUID,
    decision: str,
    reason: str | None = None,
    payload: dict[str, Any] | None = None,
    previous: dict[str, Any] | None = None,
) -> uuid.UUID:
    """A `human.decided` event in the outbox; returns its event id."""
    from tumnis.core.events import registry  # noqa: PLC0415
    from tumnis.core.outbox import emit  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    model = registry.model("human.decided", 1)
    event = model.model_validate(
        {
            "item_kind": item_kind,
            "item_id": uuid.uuid4(),
            "target_type": "task",
            "target_id": task_id,
            "decision": decision,
            "reason": reason,
            "previous": previous,
            "payload": payload,
        }
    )
    async with tenant_session(world.ctx) as s:
        return await emit(s, event, occurred_at=world.clock.now())


async def deliver(
    workspace_id: uuid.UUID, name: str, payload: dict[str, Any], at: datetime
) -> uuid.UUID:
    """The event handed to every agents subscriber of `name`; returns its event id."""
    from tumnis.core.events import EventEnvelope  # noqa: PLC0415

    envelope = EventEnvelope(
        event_id=uuid.uuid4(),
        name=name,
        schema_version=1,
        workspace_id=workspace_id,
        occurred_at=at,
        actor="system",
        payload={"schema_version": 1, **payload},
    )
    await digest_subscriber(name).handler(envelope)
    return envelope.event_id


def document_payload(
    title: str, *, project_id: uuid.UUID | None, version_no: int = 1, trust: str = "trusted"
) -> dict[str, Any]:
    """P1-16's `document.added`/`document.changed` payload."""
    return {
        "document_id": str(uuid.uuid4()),
        "version_id": str(uuid.uuid4()),
        "version_no": version_no,
        "project_id": None if project_id is None else str(project_id),
        "title": title,
        "trust": trust,
        "size": 64,
    }


async def finish_hybrid(world: DigestWorld, project: str, title: str) -> Any:
    """A Hybrid task (estimate 60) started now and done 75 minutes later."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    task = await world.task(project, title=title, label="hybrid", estimate_minutes=60)
    async with tenant_session(world.ctx) as s:
        task = await tasks.change_status(
            s,
            world.ctx.actor,
            task.id,
            tasks.Status.IN_PROGRESS,
            task.version,
            now=world.clock.now(),
        )
    world.clock.advance(timedelta(minutes=75))
    async with tenant_session(world.ctx) as s:
        return await tasks.change_status(
            s, world.ctx.actor, task.id, tasks.Status.DONE, task.version, now=world.clock.now()
        )


async def comment(
    world: DigestWorld, task_id: uuid.UUID, text: str, actor: str | None = None
) -> Any:
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.types import ActorRef  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    who = ActorRef(actor) if actor else world.ctx.actor
    async with tenant_session(WorkspaceContext(world.workspace.id, who)) as s:
        return await tasks.add_comment(s, who, task_id, text, now=world.clock.now())


class _MailConnector:
    """The smallest email connector: one raw item is one message."""

    kind: ClassVar[str] = "email"
    provider: ClassVar[str] = "digest-test"
    capabilities: ClassVar[frozenset[str]] = frozenset()

    async def sync(self, cursor: Any) -> Any:  # pragma: no cover  # never synced here
        raise NotImplementedError

    def map(self, raw: Any) -> list[Any]:
        from tumnis.modules.integrations.api import MessageRecord  # noqa: PLC0415

        return [
            MessageRecord(
                external_id=raw.external_id,
                fetched_at=raw.fetched_at,
                from_addr="client@example.com",
                to_addrs=["me@example.org"],
                subject=raw.payload["subject"],
                body_text=raw.payload["body"],
                sent_at=raw.fetched_at,
            )
        ]

    async def health(self) -> Any:  # pragma: no cover
        return "ok"


async def link_email(world: DigestWorld, task_id: uuid.UUID, subject: str, body: str) -> uuid.UUID:
    """The email ingested and linked to the task; returns the context item's id."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.integrations import api as integrations  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    ctx = world.ctx
    async with tenant_session(ctx) as s:
        connection = await integrations.seed_connection(
            s, "email", _MailConnector.provider, "me@example.org"
        )
    raw = integrations.RawItem(
        external_id=f"msg-{uuid.uuid4().hex[:10]}",
        record_type="message",
        payload={"subject": subject, "body": body},
        fetched_at=world.clock.now(),
    )
    page = integrations.SyncPage(items=[raw], next_cursor=None, has_more=False)
    result = await integrations.ingest_page(ctx, connection, _MailConnector(), page)  # type: ignore[arg-type]
    [message_id] = result.changed_ids
    async with tenant_session(ctx) as s:
        item = await integrations.link_context(
            ctx,
            owner_type="task",
            owner_id=task_id,
            target_type="message",
            target_id=message_id,
            added_by=ctx.actor,
            session=s,
        )
        await tasks.link_context_item(s, ctx.actor, task_id, item.id, now=world.clock.now())
    return item.id


def digest_subscriber(event: str) -> Subscriber:
    import tumnis.wiring  # noqa: F401, PLC0415  # every module's subscribers register
    from tumnis.core.events import subscribers_for  # noqa: PLC0415

    [sub] = [s for s in subscribers_for(event) if s.module == "agents"]
    return sub


def _owner_rows(db: DbUrls, query: str) -> list[dict[str, Any]]:
    with psycopg.connect(db.libpq(OWNER), row_factory=dict_row) as conn:
        return conn.execute(query.encode()).fetchall()


async def drain(db: DbUrls) -> None:
    """Relays every pending outbox row and waits for the agents deliveries."""
    from dbos import DBOS  # noqa: PLC0415

    import tumnis.wiring  # noqa: F401, PLC0415
    from tumnis.core.events import delivery_id, relay_once, subscribers_for  # noqa: PLC0415

    pending = _owner_rows(db, "SELECT event_id, name FROM outbox WHERE sent_at IS NULL")
    while await relay_once():
        pass
    for row in pending:
        for sub in subscribers_for(row["name"]):
            if sub.module == "agents":
                handle: WorkflowHandleAsync[Any] = await DBOS.retrieve_workflow_async(
                    delivery_id(row["event_id"], sub.name)
                )
                await handle.get_result()


@dataclass
class DigestConsumer:
    """One consumer (an API key) reading one digest the way the digest skill does."""

    client: httpx.AsyncClient
    scope: str
    project_id: uuid.UUID | None = None
    cursor: str | None = None
    limit: int = 200

    @property
    def path(self) -> str:
        return DIGEST_PATHS[self.scope].format(project_id=self.project_id)

    async def page(self, since: str | None) -> httpx.Response:
        params: dict[str, Any] = {"limit": self.limit}
        if since is not None:
            params["since"] = since
        return await self.client.get(self.path, params=params)

    async def read(self) -> list[dict[str, Any]]:
        """Every entry since the last acknowledged read (acknowledging it), page by page."""
        entries: list[dict[str, Any]] = []
        while True:
            response = await self.page(self.cursor)
            response.raise_for_status()
            body = response.json()
            entries.extend(body["entries"])
            self.cursor = body["next_cursor"]
            if not body["has_more"]:
                return entries


def blocks(text: str) -> list[tuple[str, str]]:
    """(open tag attributes, raw escaped content) of each untrusted block in `text`."""
    return [(m["attrs"], m["body"]) for m in _BLOCK.finditer(text)]
