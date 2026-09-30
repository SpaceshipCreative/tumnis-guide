"""Helpers for the enrichment tests (P1-08). No assertions: they make rows through the
modules' apis, wire the fakes, relay and wait, so the code under test may change without
touching a locked test body.

- `agent_project(fake_runner, runner, profile, db)`: a project whose agent profile lives on
  `runner` and is provisioned (`ready`), as P1-06 leaves it.
- `new_task(workspace, clock, project_id, title, **fields)`: `tasks.api.create_task` as the
  workspace's user (it emits `task.created`).
- `enrichment_settings(clock, ...)`: the enrichment's clock and timeouts (R-30) and a
  Generation fake for the placeholder, restored afterwards.
- `recorded(name)`: a scripted runner result from `tests/fakes/recordings/runner`.
- `task_row(db, task_id)`, `rows(db, query, *params)`: reads as the owner.
- `until(check)`: polls a sync or async check until it returns something truthy.
- `LiveListener`: LISTENs on the live channel `/ws` forwards (`tumnis_live`, P0-22) and
  keeps every `{ws, entity, id}` it hears.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from tests._pg import OWNER

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunner, FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

RUNNER_RECORDINGS = Path(__file__).resolve().parents[5] / "tests/fakes/recordings/runner"
PLACEHOLDER = "Open the invoice template"
SETTLE_S = 30.0


def recorded(name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((RUNNER_RECORDINGS / f"{name}.result.json").read_text())
    return body


def rows(db: DbUrls, query: str, *params: Any) -> list[dict[str, Any]]:
    with psycopg.connect(db.libpq(OWNER), row_factory=dict_row) as conn:
        return list(conn.execute(query.encode(), params).fetchall())


def execute(db: DbUrls, query: str, *params: Any) -> None:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        conn.execute(query.encode(), params)


def task_row(db: DbUrls, task_id: UUID) -> dict[str, Any]:
    [row] = rows(db, "SELECT * FROM tasks WHERE id = %s", task_id)
    return row


async def until[T](
    check: Callable[[], Awaitable[T] | T], *, timeout_s: float = SETTLE_S, poll_s: float = 0.1
) -> T:
    """The first truthy value of `check`, or its last value once `timeout_s` has passed."""
    deadline = time.monotonic() + timeout_s
    while True:
        value = check()
        if asyncio.iscoroutine(value) or isinstance(value, Awaitable):
            value = await value
        if value or time.monotonic() >= deadline:
            return value
        await asyncio.sleep(poll_s)


def user_ctx(workspace: WorkspaceHandle) -> Any:
    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.core.types import ActorRef  # noqa: PLC0415

    return WorkspaceContext(workspace.id, ActorRef(f"user:{workspace.user_id}"))


def agent_project(
    fake_runner: FakeRunnerFactory, runner: FakeRunner, profile: str, db: DbUrls
) -> UUID:
    """A project with its agent profile on `runner`, provisioned (status `ready`)."""
    profile_id = fake_runner.register_profile(profile, runner=runner)
    execute(db, "UPDATE agent_profiles SET status = 'ready' WHERE id = %s", profile_id)
    [row] = rows(db, "SELECT project_id FROM agent_profiles WHERE id = %s", profile_id)
    project_id: UUID = row["project_id"]
    return project_id


async def new_task(
    workspace: WorkspaceHandle, clock: FixedClock, project_id: UUID, title: str, **fields: Any
) -> Any:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    ctx = user_ctx(workspace)
    async with tenant_session(ctx) as s:
        return await tasks.create_task(
            s,
            ctx.actor,
            tasks.TaskCreate(project_id=project_id, title=title, **fields),
            now=clock.now(),
        )


async def relay() -> None:
    """Relay the outbox once (task.created and friends reach their subscribers)."""
    from tumnis.core.events import relay_once  # noqa: PLC0415

    await relay_once()


@contextlib.contextmanager
def enrichment_settings(
    clock: FixedClock,
    *,
    placeholder: str | None = PLACEHOLDER,
    generation_delay_ms: int = 0,
    placeholder_timeout_ms: int = 2000,
    label_wait_s: float = 2.0,
    run_timeout_s: int = 20,
) -> Iterator[Any]:
    """The enrichment's clock and timeouts, and a Generation fake answering `placeholder`
    (after `generation_delay_ms`); both restored afterwards. Yields the fake."""
    from tumnis.core.adapters.registry import resolve  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415
    from tumnis.modules.decisions import api as decisions  # noqa: PLC0415
    from tumnis.settings import GenerationSettings  # noqa: PLC0415

    generation = resolve("decisions.vllm_generation", "fake")  # modules' fakes via the registry
    generation.script(placeholder or "", delay_ms=generation_delay_ms)
    decisions.configure_generation(
        GenerationSettings(placeholder_timeout_ms=placeholder_timeout_ms), provider=generation
    )
    agents.configure_enrichment(clock=clock, label_wait_s=label_wait_s, run_timeout_s=run_timeout_s)
    try:
        yield generation
    finally:
        agents.configure_enrichment()
        decisions.configure_generation(GenerationSettings())


class LiveListener:
    """LISTEN tumnis_live in a thread; `heard` holds each notification's payload."""

    def __init__(self, db: DbUrls) -> None:
        self.db = db
        self.heard: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._listen, daemon=True)

    def _listen(self) -> None:
        with psycopg.connect(self.db.libpq(OWNER), autocommit=True) as conn:
            conn.execute(b"LISTEN tumnis_live")
            self._ready.set()
            while not self._stop.is_set():
                for note in conn.notifies(timeout=0.2):
                    self.heard.append(json.loads(note.payload))

    def __enter__(self) -> LiveListener:
        self._thread.start()
        self._ready.wait(10)
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(5)

    def of(self, entity: str, entity_id: UUID) -> int:
        return sum(
            1 for n in self.heard if n.get("entity") == entity and n.get("id") == str(entity_id)
        )
