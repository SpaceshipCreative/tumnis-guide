"""Helpers for the P2-08 taint spec tests (SAF-1). No assertions live here: spec-guard locks
the test bodies, and these helpers adapt to the apis they call.

- `context_item(ctx, project_id, tainted=...)`: a ContextItem owned by the project, made
  through `integrations.api.link_context`: a bare URL (outside content, so tainted) or a
  text document written in the app (trusted, so not tainted).
- `Runs(fake_runner, world)`: real runs of tasks. The first call registers a project
  profile on a fake runner (scripted to succeed) and gives it a key; `run_of(task_id)`
  builds the task's packet with `build_packet` (R-24), runs it through `run_skill` and
  answers the run's id once it has ended. `caller(run_id, project)` is the agent surface's
  caller for a fresh task token of that run (the run's own token ended with it).
- `taint_of(table, row_id)`: a row's stored `tainted`, read as the owner role.
"""

from __future__ import annotations

import asyncio
import itertools
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from tests._mcp import World
    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunner, FakeRunnerFactory
    from tumnis.core.tenancy import WorkspaceContext

PROFILE: Final = "taint-site"
SKILL: Final = "work"
TOKEN_SCOPES: Final = frozenset({"tasks:read", "tasks:write", "context:read", "knowledge:write"})
_ids = itertools.count(1)


async def context_item(ctx: WorkspaceContext, project_id: uuid.UUID, *, tainted: bool) -> uuid.UUID:
    """A ContextItem owned by the project: a URL when `tainted`, else a trusted text entry."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.integrations import api as integrations  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    n = next(_ids)
    if tainted:
        item = await integrations.link_context(
            ctx,
            owner_type="project",
            owner_id=project_id,
            target_type="url",
            target_url=f"https://example.com/outside/{n}-{uuid.uuid4().hex[:8]}",
            added_by=ctx.actor,
        )
        return item.id
    async with tenant_session(ctx) as s:
        document_id = await knowledge.put_text_document(
            s, project_id, title=f"House notes {n}", body_md="Written in the app.", role=None
        )
    item = await integrations.link_context(
        ctx,
        owner_type="project",
        owner_id=project_id,
        target_type="document",
        target_id=document_id,
        added_by=ctx.actor,
    )
    return item.id


@dataclass
class Runs:
    """Real runs of tasks through the fake runner (see the module docstring)."""

    fake_runner: FakeRunnerFactory
    world: World
    profile_id: uuid.UUID | None = None
    key_id: uuid.UUID | None = None
    runner: FakeRunner | None = None
    made: list[uuid.UUID] = field(default_factory=list)

    async def setup(self) -> uuid.UUID:
        from tumnis.modules.agents import api as agents  # noqa: PLC0415
        from tumnis.modules.auth import api as auth  # noqa: PLC0415

        if self.profile_id is not None:
            return self.profile_id
        workspace, clock = self.world.workspace, self.world.clock
        self.runner = self.fake_runner(profiles=[PROFILE])
        self.runner.script(PROFILE, SKILL, {"summary": "Done"})  # kept for every run
        self.profile_id = self.fake_runner.register_profile(
            PROFILE, runner=self.runner, role="project", project_id=self.world.projects["A"]
        )
        key = await auth.create_key(
            workspace.ctx,
            auth.KeyIn(name=f"{PROFILE} key", scopes=sorted(TOKEN_SCOPES)),
            now=clock.now(),
        )
        self.key_id = key.id
        await agents.set_profile_key(workspace.ctx, self.profile_id, key.id, now=clock.now())
        return self.profile_id

    async def run_of(self, task_id: uuid.UUID) -> uuid.UUID:
        """The id of a finished task run of the task, dispatched through `run_skill`."""
        from tumnis.modules.agents import workflows  # noqa: PLC0415
        from tumnis.modules.agents.api import RunKind  # noqa: PLC0415
        from tumnis.modules.agents.packet_builder import build_packet  # noqa: PLC0415

        profile_id = await self.setup()
        run_id = uuid.uuid4()
        packet = await build_packet(
            RunKind.TASK,
            task_id=task_id,
            run_id=run_id,
            profile_id=profile_id,
            ctx=self.world.workspace.ctx,
        )
        handle = await workflows.start_run_skill(self.world.workspace.id, packet)
        await asyncio.wait_for(handle.get_result(), 60)
        self.made.append(run_id)
        return run_id

    async def caller(self, run_id: uuid.UUID, project: str = "A") -> Any:
        """The agent surface's caller for a fresh task token of the run."""
        from tumnis.core import agent_surface  # noqa: PLC0415
        from tumnis.core.principal import Principal  # noqa: PLC0415
        from tumnis.modules.auth import api as auth  # noqa: PLC0415
        from tumnis.wiring import load_mcp  # noqa: PLC0415

        load_mcp()  # the caller-facts resolvers register with the modules' mcp
        await self.setup()
        assert self.key_id is not None  # set by setup
        clock = self.world.clock
        token = await auth.issue_task_token(
            self.world.workspace.ctx,
            run_id=run_id,
            project_id=self.world.projects[project],
            api_key_id=self.key_id,
            scopes=TOKEN_SCOPES,
            now=clock.now(),
        )
        principal = await auth.authenticate_bearer(token, now=clock.now())
        if not isinstance(principal, Principal):
            raise RuntimeError(f"a fresh task token did not authenticate: {principal!r}")
        return await agent_surface.resolve_caller(principal)


def taint_of(db: DbUrls, table: str, row_id: uuid.UUID) -> bool | None:
    """The row's stored `tainted` (None when there is no such row), read as the owner."""
    import psycopg  # noqa: PLC0415
    from psycopg import sql  # noqa: PLC0415

    from tests._pg import OWNER  # noqa: PLC0415

    with psycopg.connect(db.libpq(OWNER)) as conn:
        row = conn.execute(
            sql.SQL("SELECT tainted FROM {} WHERE id = %s").format(sql.Identifier(table)),
            (row_id,),
        ).fetchone()
    return None if row is None else bool(row[0])
