"""Helpers for the github integration tests (P2-13). No assertions live here: spec-guard
locks the test bodies, and this module is where their shapes are built.

- `configured(db)`: tumnis.core.db pointed at the per-test database (no pooling).
- `wired(clock)`: a fresh `FakeGitHubStatus` (every recorded pull request) and the test
  clock in the github workflows.
- `set_settings(ctx, allowed_repos=..., webhook_secret=...)`: Settings > GitHub.
- `new_task(ctx, title)`: a task in a new project, through the projects and tasks apis.
- `link(ctx, task_id, url)`: the task links the pull request (tasks api), as the drawer's
  "Link a pull request" does.
- `add_result_item(db, workspace_id, task_id, url, decided=False)`: a result review item
  whose links name `url` (the `result` kind and its intake arrive with P2-04; the row is
  written as the owner, the shape P2-04 writes).
- `rows(db, sql, *params)`: owner reads; `outbox(db, name)`: the outbox rows of an event.
- `wait_for_workflows(client, name, count=1)`: the finished workflows called `name`.
- `live_messages(db)`: the `/ws` notifications (`tumnis_live`) sent while inside.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row

from tests._pg import OWNER

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tumnis.core.clock import FixedClock
    from tumnis.core.tenancy import WorkspaceContext
    from tumnis.modules.github.adapters.fake import FakeGitHubStatus

T0 = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
WEBHOOK_SECRET = "not-a-real-webhook-secret"  # an invented value
OPEN_GREEN = "https://github.com/acme-example/site/pull/42"  # open, green, approved
MERGED = "https://github.com/acme-example/site/pull/38"
CLOSED = "https://github.com/cove-example/web/pull/3"
OPEN_RED = "https://github.com/brio-example/api/pull/7"  # open, one failed check run


@asynccontextmanager
async def configured(db: DbUrls) -> AsyncIterator[None]:
    from tumnis.core import db as core_db  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    try:
        yield
    finally:
        await core_db.dispose()


@contextmanager
def wired(clock: FixedClock) -> Iterator[FakeGitHubStatus]:
    from tumnis.modules.github import workflows  # noqa: PLC0415
    from tumnis.modules.github.adapters.fake import FakeGitHubStatus  # noqa: PLC0415

    fake = FakeGitHubStatus()
    previous = workflows.use(lambda workspace_id, settings: fake, clock)
    try:
        yield fake
    finally:
        workflows.use(*previous)


async def set_settings(
    ctx: WorkspaceContext,
    *,
    allowed_repos: list[str],
    webhook_secret: str = "",
    token: str = "",
) -> None:
    from tumnis.core.settings_store import get_setting, put_setting  # noqa: PLC0415
    from tumnis.modules.github.api import SETTINGS_SECTION, GitHubSettings  # noqa: PLC0415

    current = await get_setting(ctx, SETTINGS_SECTION, GitHubSettings)
    await put_setting(
        ctx,
        SETTINGS_SECTION,
        GitHubSettings(token=token, allowed_repos=allowed_repos, webhook_secret=webhook_secret),
        expected_version=None if current is None else current.version,
    )


async def new_task(ctx: WorkspaceContext, title: str = "Ship the booking form") -> Any:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    async with tenant_session(ctx) as s:
        project = await projects.create_project(
            s,
            SYSTEM_ACTOR,
            projects.ProjectCreate(name=f"Project {uuid.uuid4().hex[:8]}"),
            now=T0,
        )
        return await tasks.create_task(
            s, SYSTEM_ACTOR, tasks.TaskCreate(project_id=project.id, title=title), now=T0
        )


async def link(ctx: WorkspaceContext, task_id: uuid.UUID, url: str) -> Any:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    async with tenant_session(ctx) as s:
        return await tasks.link_pull_request(s, SYSTEM_ACTOR, task_id, url, now=T0)


def add_result_item(
    db: DbUrls, workspace_id: uuid.UUID, task_id: uuid.UUID, url: str, *, decided: bool = False
) -> uuid.UUID:
    payload = {
        "summary": "Drafted the change",
        "links": [{"kind": "pull_request", "url": url, "label": "PR"}],
    }
    (row,) = rows(
        db,
        "INSERT INTO review_items (workspace_id, kind, target_type, target_id, payload,"
        " decided_at, decision, created_by)"
        " VALUES (%s, 'result', 'task', %s, %s, %s, %s, 'system') RETURNING id",
        workspace_id,
        task_id,
        json.dumps(payload),
        T0 if decided else None,
        "accept" if decided else None,
    )
    item_id: uuid.UUID = row["id"]
    return item_id


def rows(db: DbUrls, query: str, *params: Any) -> list[dict[str, Any]]:
    with psycopg.connect(db.libpq(OWNER), row_factory=dict_row) as conn:
        cursor = conn.execute(query, params)
        return cursor.fetchall() if cursor.description else []


def outbox(db: DbUrls, name: str) -> list[dict[str, Any]]:
    return rows(db, "SELECT * FROM outbox WHERE name = %s ORDER BY occurred_at, id", name)


async def wait_for_workflows(
    client: Any, name: str, *, count: int = 1, timeout_s: float = 30
) -> list[Any]:
    """The workflows called `name` once `count` of them finished (any status but pending
    or enqueued); TimeoutError otherwise."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        found = await asyncio.to_thread(client.list_workflows, name=name)
        done = [w for w in found if w.status not in {"PENDING", "ENQUEUED"}]
        if len(done) >= count:
            return done
        if loop.time() > deadline:
            raise TimeoutError(f"{name}: {[w.status for w in found]}")
        await asyncio.sleep(0.05)


@asynccontextmanager
async def live_messages(db: DbUrls) -> AsyncIterator[list[dict[str, Any]]]:
    """Collects every `tumnis_live` notification (what the api's LiveHub fans out to the
    workspace's `/ws` sockets) while inside."""
    seen: list[dict[str, Any]] = []
    conn = await psycopg.AsyncConnection.connect(db.libpq(OWNER), autocommit=True)
    await conn.execute("LISTEN tumnis_live")

    async def collect() -> None:
        async for note in conn.notifies():
            seen.append(json.loads(note.payload))

    reader = asyncio.create_task(collect())
    try:
        yield seen
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader
        await conn.close()


async def until(check: Any, *, timeout_s: float = 30) -> None:
    """Waits until `check()` is true; TimeoutError otherwise."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while not check():
        if loop.time() > deadline:
            raise TimeoutError("condition not met")
        await asyncio.sleep(0.05)
