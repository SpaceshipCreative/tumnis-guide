"""A reset that waits too long for a table lock gives up and says who holds it (SEED, R-37).

The e2e stack's `POST /v1/test/reset` TRUNCATEs every table. A transaction left open in the
api or the worker (a request parked on an `await` with its locks held) made every later
reset wait forever, so the e2e job ran into its budget without a word on the cause. The
reset now bounds its lock wait and reports the open transactions and the api's parked
tasks instead."""

from __future__ import annotations

import asyncio
import importlib
from datetime import timedelta

import pytest

pytestmark = [pytest.mark.req("REL-7"), pytest.mark.wp("SEED")]


def test_blocked_report_names_the_open_transactions_and_parked_tasks() -> None:
    """T-SEED-25
    The report lists each open transaction (pid, role, application, state, what it waits
    on, its age, the pids blocking it and its last statement) and each parked api task's
    await chain, so a stuck reset says where the lock holder stopped."""
    blocked_report = importlib.import_module("tumnis.core.testing_routes").blocked_report

    activity = [
        {
            "pid": 41,
            "usename": "tumnis_app",
            "application_name": "api",
            "state": "idle in transaction",
            "wait_event_type": "Client",
            "wait_event": "ClientRead",
            "xact_age": timedelta(seconds=75),
            "blocked_by": [],
            "query": "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
        },
        {
            "pid": 42,
            "usename": "tumnis_owner",
            "application_name": "",
            "state": "active",
            "wait_event_type": "Lock",
            "wait_event": "relation",
            "xact_age": timedelta(seconds=20),
            "blocked_by": [41],
            "query": "TRUNCATE agent_profiles, board_columns",
        },
    ]
    chains = [["tumnis/modules/planning/api.py:1300 swap_item", "<Future pending>"]]

    report = blocked_report(activity, chains)

    assert "pid 41" in report
    assert "idle in transaction" in report
    assert "pg_advisory_xact_lock" in report
    assert "blocked by [41]" in report
    assert "75" in report
    assert "tumnis/modules/planning/api.py:1300 swap_item" in report


async def test_await_chain_follows_a_parked_coroutine_to_its_innermost_await() -> None:
    """T-SEED-25
    A task's own stack shows only its outermost coroutine; the chain follows each
    coroutine's `cr_await` down to the line it is parked on."""
    await_chain = importlib.import_module("tumnis.core.testing_routes").await_chain

    gate = asyncio.Event()

    async def inner() -> None:
        await gate.wait()

    async def outer() -> None:
        await inner()

    task = asyncio.create_task(outer())
    await asyncio.sleep(0)
    try:
        chain = await_chain(task)
        names = [line.rsplit(" ", 1)[-1] for line in chain]
        assert names[:2] == ["outer", "inner"]
        assert any("wait" in line for line in chain[2:])
    finally:
        gate.set()
        await task
