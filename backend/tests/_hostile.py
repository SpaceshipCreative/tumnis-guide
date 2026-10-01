"""Helpers for the hostile packet test (P2-11, SAF-6). No assertions live here: spec-guard
locks the test body, and this module is where later work packages plug in new paths.

- `hostile_items()`: every case and benign twin of backend/fixtures/hostile (its index),
  with the text parts it injects (the content, then any companions' contents).
- `hostile_world(db, workspace, clock)`: the database configured for the test, projects A
  and B through `tests._mcp.make_world`, and `world.packet(item)`, which injects one item
  through its `inject_as` path into a fresh task of project A and builds that task's
  packet with the real builder.

The injection paths, as far as the merged modules reach today:

- `context_item`: an email or chat message (a `message` record) or a note (`note`),
  ingested through `integrations.api.ingest_page` and linked to the task with
  `link_context`.
- `passage`: a tainted document passage added to the task's gathered inputs, after the
  ones `knowledge.api.passages_for` chose (P1-17).
- `task_title`: a tainted task whose title is the text (a task made from outside content).
- `digest_entry`: a comment written by an API key, which is how a digest's
  `task_commented` entry reaches a packet (P2-03 renders it the same way).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

import yaml

from tests.fixtures import REPO_ROOT

if TYPE_CHECKING:
    from tests._mcp import World
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.agents.packet_builder import TaskPacket

HOSTILE_ROOT: Final = REPO_ROOT / "backend" / "fixtures" / "hostile"
# A case's source -> (connection kind, record type) for a context item.
_CONTEXT: Final = {
    "email": ("email", "message"),
    "chat": ("chat", "message"),
    "note": ("notes", "note"),
}


@dataclass(frozen=True)
class Part:
    source: str
    inject_as: str
    envelope: dict[str, str]
    content: str


@dataclass(frozen=True)
class HostileItem:
    """A case or a twin: its id and the parts it injects into one task."""

    id: str
    parts: tuple[Part, ...] = field(default_factory=tuple)

    @property
    def texts(self) -> list[str]:
        return [part.content for part in self.parts]


def _item(doc: dict[str, Any]) -> HostileItem:
    main = Part(doc["source"], doc["inject_as"], dict(doc.get("envelope") or {}), doc["content"])
    extra = [
        Part(c["source"], c["inject_as"], dict(c.get("envelope") or {}), c["content"])
        for c in doc.get("companions") or []
    ]
    return HostileItem(doc["id"], (main, *extra))


def hostile_items() -> list[HostileItem]:
    """Every case, then its twin, in index order."""
    index = yaml.safe_load((HOSTILE_ROOT / "index.yaml").read_text(encoding="utf-8"))
    items = []
    for entry in index["cases"]:
        for key in ("case", "twin"):
            doc = yaml.safe_load((HOSTILE_ROOT / entry[key]).read_text(encoding="utf-8"))
            items.append(_item(doc))
    return items


class HostileWorld:
    def __init__(self, db: DbUrls, workspace: WorkspaceHandle, world: World) -> None:
        self.db = db
        self.workspace = workspace
        self.world = world
        self.clock = world.clock

    async def _link(self, task_id: uuid.UUID, part: Part) -> None:
        from tumnis.modules.integrations.adapters.fake import ScriptedConnector  # noqa: PLC0415
        from tumnis.modules.integrations.api import ingest_page, link_context  # noqa: PLC0415
        from tumnis.modules.integrations.tests.integration._integrations import (  # noqa: PLC0415
            item,
            new_connection,
            page,
            rows,
        )

        if part.source not in _CONTEXT:
            raise ValueError(f"no context item path for a {part.source} part yet")
        kind, record_type = _CONTEXT[part.source]
        connection = new_connection(self.db, self.workspace.id, kind=kind)
        external = f"hostile-{uuid.uuid4().hex[:12]}"
        envelope = part.envelope
        if record_type == "note":
            raw = item(external, "note", title=envelope.get("title"), text=part.content)
        else:
            sender = envelope.get("from")
            subject = envelope.get("subject") or envelope.get("channel")
            raw = item(external, "message", subject=subject, text=part.content, **{"from": sender})
        await ingest_page(self.workspace.ctx, connection, ScriptedConnector(kind=kind), page(raw))
        [record] = rows(self.db, "notes" if record_type == "note" else "messages", connection)
        await link_context(
            self.workspace.ctx,
            owner_type="task",
            owner_id=task_id,
            target_type=record_type,  # type: ignore[arg-type]
            target_id=record["id"],
            added_by="user",
        )

    async def packet(self, hostile: HostileItem) -> TaskPacket:
        from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
        from tumnis.core.types import ActorRef  # noqa: PLC0415
        from tumnis.modules.agents.packet_builder import (  # noqa: PLC0415
            PassageInput,
            assemble,
            gather_inputs,
            new_nonce,
        )
        from tumnis.modules.agents.rules import RunKind  # noqa: PLC0415
        from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

        user = ActorRef(f"user:{self.workspace.user_id}")
        ctx = WorkspaceContext(self.workspace.id, user)
        title = next((p.content for p in hostile.parts if p.inject_as == "task_title"), None)
        async with tenant_session(ctx) as s:
            task = await tasks.create_task(
                s,
                user,
                tasks.TaskCreate(
                    project_id=self.world.projects["A"],
                    title=(title or f"Reply to the thread {uuid.uuid4().hex[:6]}").strip(),
                    label="ai",
                ),
                now=self.clock.now(),
                tainted=title is not None,
            )
        passages: list[PassageInput] = []
        for part in hostile.parts:
            if part.inject_as == "context_item":
                await self._link(task.id, part)
            elif part.inject_as == "digest_entry":
                key = ActorRef(f"api_key:{uuid.uuid4()}")
                async with tenant_session(WorkspaceContext(self.workspace.id, key)) as s:
                    await tasks.add_comment(s, key, task.id, part.content, now=self.clock.now())
            elif part.inject_as == "passage":
                heading = part.envelope.get("title")
                passages.append(
                    PassageInput(
                        document_id=str(uuid.uuid4()),
                        text=part.content,
                        heading_path=[heading] if heading else [],
                        page_from=1,
                        tainted=True,
                    )
                )
            elif part.inject_as != "task_title":
                raise ValueError(f"unknown injection point {part.inject_as!r}")
        async with tenant_session(ctx) as s:
            inputs = await gather_inputs(s, task.id)
        inputs = inputs.model_copy(update={"passages": [*inputs.passages, *passages]})
        return assemble(
            inputs,
            kind=RunKind.TASK,
            run_id=uuid.uuid4(),
            profile_id=uuid.uuid4(),
            nonce=new_nonce(),
            token=None,
        )


@asynccontextmanager
async def hostile_world(
    db: DbUrls, workspace: WorkspaceHandle, clock: FixedClock
) -> AsyncIterator[HostileWorld]:
    from tests._mcp import make_world  # noqa: PLC0415
    from tumnis.modules.integrations.tests.integration._integrations import (  # noqa: PLC0415
        configured,
    )

    async with configured(db):
        yield HostileWorld(db, workspace, await make_world(workspace, clock))
