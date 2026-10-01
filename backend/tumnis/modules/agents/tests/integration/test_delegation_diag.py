"""TEMPORARY diagnostics for P2-06 (not a spec test; deleted before review). Mirrors
T-P2-06-09 and A2.4 step by step and fails with the state at the first step that differs."""

from __future__ import annotations

import asyncio
import concurrent.futures
import time
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest

from tests.acceptance._phase2 import (
    decide,
    human_wait_poll_seconds,
    post_result,
    review_items,
    tool,
    wait_id,
)
from tests.acceptance._phase2 import relay as relay2
from tumnis.modules.agents.tests.integration._delegation import (
    delegate,
    run_row,
    start_master_run,
    stop,
    wait,
    world,
)
from tumnis.modules.agents.tests.integration._runs import owner_rows, relay, wait_until

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.req("FR-5.2"),
    pytest.mark.wp("P2-06"),
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]

QUESTION = "Should the footer link open in a new tab?"


def _wf(workflow_id: str) -> str:
    from dbos import DBOS  # noqa: PLC0415

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        status = pool.submit(DBOS.get_workflow_status, workflow_id).result()
    if status is None:
        return "no workflow"
    return f"{status.status} name={status.name} error={status.error!r} output={status.output!r}"


def _state(db: DbUrls, run_id: UUID, task_id: UUID) -> str:
    run = owner_rows(
        db,
        "SELECT status, stop_reason, workflow_id, profile_id, state_seq FROM runs WHERE id = %s",
        (run_id,),
    )
    wf = _wf(str(run[0][2])) if run and run[0][2] else "no workflow id"
    signals = owner_rows(
        db,
        "SELECT name, payload, sent_at FROM outbox WHERE payload::text LIKE %s ORDER BY id",
        (f"%{run_id}%",),
    )
    items = owner_rows(
        db,
        "SELECT kind, target_id, payload, decided_at FROM review_items WHERE target_id = %s",
        (task_id,),
    )
    profiles = owner_rows(
        db, "SELECT id, role, name, runner_id, transport, status FROM agent_profiles", ()
    )
    runs = owner_rows(
        db, "SELECT id, status, stop_reason, profile_id, workflow_id FROM runs ORDER BY id", ()
    )
    return (
        f"\nrun={run}\nworkflow={wf}\nsignals={signals}\nitems={items}"
        f"\nprofiles={profiles}\nruns={runs}"
    )


async def test_diag_loop(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    w = await world(fake_runner, workspace, clock)
    master = w.master_key or ""
    task = await w.ai_task("Fix footer link")
    async with relay(db):
        master_run = await start_master_run(w, db)
        await asyncio.sleep(1)
        assert run_row(db, master_run)[0] == "running", _state(db, master_run, task)  # type: ignore[index]
        for i in range(2):
            delegated = await delegate(w, master, task)
            assert delegated.ok, (i, delegated, _state(db, master_run, task))
            child = UUID(delegated.data["delegation_id"])
            await w.delivered(child)
            await stop(w, child)
            ended = await wait_until(
                lambda child=child: (
                    (run_row(db, child) or ("x",))[0]
                    not in ("queued", "held", "running", "waiting_on_human")
                )
            )
            assert ended, (i, "child did not end", _state(db, child, task))
        looped = await delegate(w, master, task)
        assert looped.code == "delegation_loop", (looped, _state(db, master_run, task))
        done = await wait_until(
            lambda: (
                (run_row(db, master_run) or ("x",))[0]
                not in ("queued", "held", "running", "waiting_on_human")
            )
        )
        assert done, ("master run did not end", _state(db, master_run, task))
    row = run_row(db, master_run)
    assert row is not None
    assert row[:2] == ("cancelled", "delegation_loop"), (row, _state(db, master_run, task))


async def test_diag_a2_4(  # noqa: PLR0917
    db: DbUrls,
    dbos: Any,
    clock: FixedClock,
    fakes: Any,
    app: Any,
    workspace: WorkspaceHandle,
    fake_runner: FakeRunnerFactory,
    session_client: SessionClient,
) -> None:
    human_wait_poll_seconds(2)
    w = await world(fake_runner, workspace, clock)
    master = w.master_key or ""
    task = await w.ai_task("Fix footer link")
    async with relay2(db):
        delegated = await delegate(w, master, task)
        assert delegated.ok, delegated
        d = UUID(delegated.data["delegation_id"])
        await w.delivered(d)
        token = w.token(d)
        waiting = asyncio.create_task(wait(w, master, d, 30))
        asked_at = time.monotonic()
        asked = await tool(w.runner, token, "ask_human", {"run_id": str(d), "prompt": QUESTION})
        first = await waiting
        waited = time.monotonic() - asked_at
        assert asked.ok, (asked, _state(db, d, task))
        assert first.ok, (first, _state(db, d, task))
        assert first.data["status"] == "waiting_on_human", (first, asked, _state(db, d, task))
        assert first.data["question"] == QUESTION, first
        assert waited < 5, (waited, first, asked)

        items = await review_items(session_client, "question")
        assert len(items) == 1, (items, _state(db, d, task))
        answered = await decide(session_client, items[0], "answer", {"answer": "Yes"})
        assert answered.status_code == 200, answered.text

        last: list[Any] = []

        async def child_answered() -> Any:
            again = await tool(
                w.runner,
                token,
                "ask_human",
                {"run_id": str(d), "prompt": QUESTION, "question_id": wait_id(asked.data)},
            )
            last.append(again)
            return again if again.ok and again.data["status"] == "answered" else None

        deadline = time.monotonic() + 15
        got = None
        while got is None and time.monotonic() < deadline:
            got = await child_answered()
            if got is None:
                await asyncio.sleep(0.05)
        assert got, (last[-3:], asked, _state(db, d, task))
        posted = await post_result(w.runner, token, d, "The footer link opens in a new tab")
        assert posted.ok, (posted, _state(db, d, task))
        done = await wait(w, master, d, 30)
    assert done.ok, done
    assert done.data["status"] == "done", (done, _state(db, d, task))
    assert done.data["run_status"] == "succeeded", done
    assert done.data["result_summary"] == "The footer link opens in a new tab", done
