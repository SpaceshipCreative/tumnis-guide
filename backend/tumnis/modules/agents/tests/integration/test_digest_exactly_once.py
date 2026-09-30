"""Exactly once, even while writes commit out of order (P2-03, FR-13.1).

A Hypothesis state machine holds three writer sessions (app role, explicit transactions,
each writing through the real `append_entry` inside its own open transaction) and two
consumers reading the project digest through the real op, each passing its last
`next_cursor`. Writers begin, insert, commit and roll back in any interleaving; a consumer
may also lose a response (read, then drop it and keep its old cursor). The model is the
set of committed entries: each consumer keeps what it reads, and at the end the entries it
kept are exactly the committed set, with no duplicate and nothing rolled back.

The machine owns an `asyncio.Runner`, so each rule runs its database work to completion
before the next one. The `db` fixture is shared by every example, so each example uses a
fresh workspace.
"""

from __future__ import annotations

import asyncio
import contextvars
import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule, run_state_machine_as_test

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tests._pg import DbUrls
    from tests.fixtures import MasterKeyFile

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

WRITERS = 3
CONSUMERS = 2
PAGE = 3  # small pages, so reads page and stop mid-way
AT = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)


class DigestMachine(RuleBasedStateMachine):
    def __init__(self, db: DbUrls, runner: asyncio.Runner) -> None:
        super().__init__()
        from tests.fixtures import make_workspace  # noqa: PLC0415
        from tumnis.core.agent_surface import Caller, get_op  # noqa: PLC0415
        from tumnis.core.principal import Principal  # noqa: PLC0415
        from tumnis.core.tenancy import WorkspaceContext, use_workspace  # noqa: PLC0415
        from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415
        from tumnis.wiring import load_mcp  # noqa: PLC0415

        load_mcp()
        self.runner = runner
        self.workspace_id = make_workspace(db, f"Digest machine {uuid.uuid4().hex[:6]}")
        self.ctx = WorkspaceContext(self.workspace_id, SYSTEM_ACTOR)
        self._scope = use_workspace(self.ctx)
        self._scope.__enter__()
        # The runner outlives this example; its default context is the one it saw first, so
        # each rule runs in this machine's own copy, which holds this example's workspace.
        self._context = contextvars.copy_context()
        self.project_id = self._run(self._make_project())
        self.op = get_op("get_project_digest")
        self.callers = [
            Caller(
                Principal(
                    kind="api_key",
                    workspace_id=self.workspace_id,
                    subject_id=uuid.uuid4(),
                    scopes=frozenset({"tasks:read"}),
                )
            )
            for _ in range(CONSUMERS)
        ]
        self.writers: list[AsyncSession | None] = [None] * WRITERS
        self.pending: list[list[uuid.UUID]] = [[] for _ in range(WRITERS)]
        self.committed: set[uuid.UUID] = set()
        self.rolled_back: set[uuid.UUID] = set()
        self.cursors: list[str | None] = [None] * CONSUMERS
        self.kept: list[list[uuid.UUID]] = [[] for _ in range(CONSUMERS)]

    def _run(self, coro: Any) -> Any:
        return self.runner.run(coro, context=self._context)

    async def _make_project(self) -> uuid.UUID:
        """The project whose digest the consumers read (a digest of a project the
        workspace does not have is 404)."""
        from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
        from tumnis.modules.projects import api as projects  # noqa: PLC0415

        async with tenant_session(self.ctx) as s:
            made = await projects.create_project(
                s,
                self.ctx.actor,
                projects.ProjectCreate(name=f"Digest machine {uuid.uuid4().hex[:6]}"),
                now=AT,
            )
        return made.id

    # --- writers -------------------------------------------------------------------------

    @rule(w=st.integers(0, WRITERS - 1))
    def begin(self, w: int) -> None:
        if self.writers[w] is not None:
            return
        from sqlalchemy import text  # noqa: PLC0415

        from tumnis.core import db  # noqa: PLC0415

        async def go() -> AsyncSession:
            session = db.app_sessionmaker()()
            await session.begin()
            await session.execute(text("SELECT 1"))  # the context applies at begin
            return session

        self.writers[w] = self._run(go())

    @rule(w=st.integers(0, WRITERS - 1))
    def insert(self, w: int) -> None:
        session = self.writers[w]
        if session is None:
            return
        from tumnis.modules.agents.api import append_entry  # noqa: PLC0415
        from tumnis.modules.agents.rules import EntrySpec  # noqa: PLC0415

        event_id = uuid.uuid4()
        spec = EntrySpec(
            kind="task_changed",
            scope="project",
            project_id=self.project_id,
            task_id=None,
            data={"change": "machine"},
        )
        self._run(append_entry(session, spec, event_id=event_id, occurred_at=AT))
        self.pending[w].append(event_id)

    def _finish(self, w: int, *, commit: bool) -> None:
        session = self.writers[w]
        if session is None:
            return

        async def go() -> None:
            if commit:
                await session.commit()
            else:
                await session.rollback()
            await session.close()

        self._run(go())
        (self.committed if commit else self.rolled_back).update(self.pending[w])
        self.pending[w] = []
        self.writers[w] = None

    @rule(w=st.integers(0, WRITERS - 1))
    def commit(self, w: int) -> None:
        self._finish(w, commit=True)

    @rule(w=st.integers(0, WRITERS - 1))
    def rollback(self, w: int) -> None:
        self._finish(w, commit=False)

    # --- consumers -----------------------------------------------------------------------

    def _read(self, c: int) -> Any:
        from tumnis.core.agent_surface import invoke  # noqa: PLC0415

        raw = {"project_id": str(self.project_id), "since": self.cursors[c], "limit": PAGE}
        return self._run(invoke(self.op, self.callers[c], raw, door="mcp", now=AT))

    @rule(c=st.integers(0, CONSUMERS - 1))
    def read(self, c: int) -> None:
        out = self._read(c)
        self.kept[c].extend(e.event_id for e in out.entries)
        self.cursors[c] = out.next_cursor

    @rule(c=st.integers(0, CONSUMERS - 1))
    def lose_response(self, c: int) -> None:
        self._read(c)  # read, then the answer never arrives: the cursor stays

    @invariant()
    def nothing_twice_nothing_rolled_back(self) -> None:
        for kept in self.kept:
            assert len(kept) == len(set(kept))
            assert not set(kept) & self.rolled_back
            assert set(kept) <= self.committed

    def teardown(self) -> None:
        try:
            from tumnis.modules.agents.tests.integration._digest import settle  # noqa: PLC0415

            for w in range(WRITERS):
                self._finish(w, commit=True)
            self._run(settle())  # another worker's open transaction only delays the horizon
            for c in range(CONSUMERS):
                for _ in range(10_000):
                    out = self._read(c)
                    self.kept[c].extend(e.event_id for e in out.entries)
                    self.cursors[c] = out.next_cursor
                    if not out.has_more and not out.entries:
                        break
                assert Counter(self.kept[c]) == Counter(self.committed), c
        finally:
            self._scope.__exit__(None, None, None)


@pytest.mark.req("FR-13.1")
@pytest.mark.wp("P2-03")
class TestDigestMachine:
    """T-P2-03-01"""

    def test_each_committed_entry_once_per_consumer(
        self, db: DbUrls, master_key_file: MasterKeyFile
    ) -> None:
        """T-P2-03-01
        With interleaved writers, commits, rollbacks, reads and lost responses, each
        committed entry is delivered to each consumer exactly once, none skipped, and no
        rolled-back entry is ever delivered."""
        from tumnis.core import db as core_db  # noqa: PLC0415

        core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
        with asyncio.Runner() as runner:
            run_state_machine_as_test(  # type: ignore[no-untyped-call]
                lambda: DigestMachine(db, runner),
                settings=settings(max_examples=60, stateful_step_count=40, deadline=None),
            )

    def test_out_of_order_commit_is_not_skipped(
        self, db: DbUrls, master_key_file: MasterKeyFile
    ) -> None:
        """T-P2-03-01
        The pinned example: writer 0 takes its transaction ID first but commits after
        writer 1, and a consumer reads in between. Had that read returned writer 1's entry,
        its cursor would sit past writer 0's entry, which would never be delivered; the
        horizon holds writer 1's entry back until writer 0 has finished."""
        from tumnis.core import db as core_db  # noqa: PLC0415

        core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
        with asyncio.Runner() as runner:
            machine = DigestMachine(db, runner)
            machine.begin(0)
            machine.insert(0)
            machine.begin(1)
            machine.insert(1)
            machine.commit(1)
            machine.read(0)
            machine.nothing_twice_nothing_rolled_back()
            machine.commit(0)
            machine.read(0)
            machine.read(0)
            machine.nothing_twice_nothing_rolled_back()
            machine.teardown()
