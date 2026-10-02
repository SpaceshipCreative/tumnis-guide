"""TEMPORARY diagnostic (FIX-followups-2): where T-P0-07-07's NOTIFY-to-delivery second
goes on a CI runner. Fails on purpose with the timings; dropped before review."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import gc
import statistics
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _connect(dsn: str) -> psycopg.Connection[Any]:
    return psycopg.connect(dsn, autocommit=True)


def _count(conn: psycopg.Connection[Any]) -> int:
    row = conn.execute("SELECT count(*) FROM test_deliveries").fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.req("ADR-0011")
@pytest.mark.wp("P0-07")
async def test_relay_latency_probe(  # noqa: PLR0915
    db: DbUrls, dbos: type[DBOS], workspace: WorkspaceHandle, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tumnis.core import events, modules  # noqa: PLC0415
    from tumnis.core.events import TestPingV1, relay_forever  # noqa: PLC0415
    from tumnis.core.outbox import emit  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.core.tests.integration import _deliveries  # noqa: PLC0415

    marks: list[tuple[str, float]] = []

    def mark(name: str) -> None:
        marks.append((name, time.monotonic()))

    real_relay_once = events.relay_once
    real_enqueue = events._enqueue
    real_enabled = modules.enabled

    async def relay_once(*a: Any, **k: Any) -> int:
        mark("relay_once.start")
        n = await real_relay_once(*a, **k)
        mark(f"relay_once.end n={n}")
        return n

    async def enqueue(*a: Any, **k: Any) -> None:
        mark("enqueue.start")
        await real_enqueue(*a, **k)
        mark("enqueue.end")

    async def enabled(*a: Any, **k: Any) -> bool:
        mark("enabled.start")
        out = await real_enabled(*a, **k)
        mark("enabled.end")
        return out

    monkeypatch.setattr(events, "relay_once", relay_once)
    monkeypatch.setattr(events, "_enqueue", enqueue)
    monkeypatch.setattr(modules, "enabled", enabled)
    for name in ("testa.record", "testb.record"):
        sub = events._subscribers[name]
        real = sub.handler

        async def handler(env: Any, _real: Any = real, _name: str = name) -> None:
            mark(f"handler.start {_name}")
            await _real(env)
            mark(f"handler.end {_name}")

        monkeypatch.setitem(events._subscribers, name, dataclasses.replace(sub, handler=handler))

    from dbos._dbos import _get_dbos_instance  # noqa: PLC0415

    sys_db = _get_dbos_instance()._sys_db
    real_start_queued = sys_db.start_queued_workflows

    def start_queued(queue: Any, *a: Any, **k: Any) -> Any:
        if queue.name != "events":
            return real_start_queued(queue, *a, **k)
        t = time.monotonic()
        out = real_start_queued(queue, *a, **k)
        if out:
            marks.append((f"dequeue.start n={len(out)}", t))
            mark("dequeue.end")
        return out

    monkeypatch.setattr(sys_db, "start_queued_workflows", start_queued)
    gc_t: list[float] = []

    def on_gc(phase: str, info: dict[str, Any]) -> None:
        if phase == "start":
            gc_t.append(time.monotonic())
        elif gc_t:
            took = time.monotonic() - gc_t.pop()
            if took > 0.02:
                mark(f"gc gen{info.get('generation')} {took:.3f}s")

    gc.callbacks.append(on_gc)
    _deliveries.create_table(db.libpq(OWNER))
    start = time.monotonic()
    stop = asyncio.Event()
    task = asyncio.create_task(relay_forever(stop, poll_s=30))
    await asyncio.sleep(0.5)
    startup = [(n, round(t - start, 3)) for n, t in marks]
    conn: psycopg.Connection[Any] = await asyncio.to_thread(_connect, db.libpq(OWNER))
    runs: list[list[tuple[str, float]]] = []
    totals: list[float] = []
    try:
        for i in range(200):
            marks.clear()
            t0 = time.monotonic()
            async with tenant_session(workspace.ctx) as s:
                await emit(s, TestPingV1(note=f"p{i}"), occurred_at=datetime.now(UTC))
            t1 = time.monotonic()
            want = 2 * (i + 1)
            while await asyncio.to_thread(_count, conn) < want:
                if time.monotonic() - t1 > 5:
                    break
                await asyncio.sleep(0.02)
            t2 = time.monotonic()
            totals.append(round(t2 - t1, 3))
            runs.append([("emit", round(t1 - t0, 3))] + [(n, round(t - t1, 3)) for n, t in marks])
            await asyncio.sleep(0.1)
    finally:
        stop.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await asyncio.to_thread(conn.close)
        gc.callbacks.remove(on_gc)
    ordered = sorted(totals)
    pct = {q: ordered[int(q * (len(ordered) - 1))] for q in (0.5, 0.9, 0.99, 1.0)}
    slow = sorted(range(len(totals)), key=lambda i: totals[i])[-8:]
    report = [f"startup={startup}", f"pct={pct}", f"median={statistics.median(totals)}"]
    report += [f"run{i} total={totals[i]}: {runs[i]}" for i in slow]
    pytest.fail("RELAY PROBE\n" + "\n".join(report))
