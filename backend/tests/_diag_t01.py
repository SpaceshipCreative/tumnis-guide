# ruff: noqa: S607, E501
"""TEMPORARY diagnostics for T-P1-07-01 (ci-health): loaded only with `-p tests._diag_t01`.
Prints, after the test's call, a per-task breakdown of the label's path from the database
and DBOS's tables, the event loop's lag, the POST times, CPU use and running containers.
Changes nothing the test asserts."""

from __future__ import annotations

import asyncio
import contextlib
import os
import resource
import statistics
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest

LAG: list[float] = []
POSTS: list[tuple[float, float, str]] = []
CPU: dict[str, Any] = {}


def _stat() -> list[int]:
    with open("/proc/stat") as f:
        return [int(x) for x in f.readline().split()[1:]]


def _docker_ps() -> str:
    try:
        out = subprocess.run(
            ["docker", "ps", "--format", "{{.Image}} {{.Status}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return out.stdout.strip().replace("\n", " | ")
    except Exception as exc:
        return f"docker ps failed: {exc}"


@pytest.fixture(autouse=True)
async def _diag_loop_lag() -> AsyncIterator[None]:
    LAG.clear()
    POSTS.clear()
    stop = asyncio.Event()

    async def monitor() -> None:
        loop = asyncio.get_running_loop()
        while not stop.is_set():
            t = loop.time()
            await asyncio.sleep(0.005)
            LAG.append((loop.time() - t - 0.005) * 1000)

    from tests import _auth  # noqa: PLC0415

    original = _auth.SessionClient.post

    async def timed_post(self: Any, url: str, *args: Any, **kwargs: Any) -> Any:
        start = time.time()
        try:
            return await original(self, url, *args, **kwargs)
        finally:
            title = (kwargs.get("json") or {}).get("title", url)
            POSTS.append((start, time.time(), str(title)))

    _auth.SessionClient.post = timed_post  # type: ignore[method-assign]
    CPU["ps_before"] = _docker_ps()
    CPU["load_before"] = os.getloadavg()
    CPU["stat_before"] = _stat()
    CPU["ru_before"] = resource.getrusage(resource.RUSAGE_SELF)
    CPU["wall_before"] = time.time()
    task = asyncio.create_task(monitor())
    try:
        yield
    finally:
        stop.set()
        with contextlib.suppress(Exception):
            await task
        _auth.SessionClient.post = original  # type: ignore[method-assign]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Iterator[None]:
    yield
    if "label_p95" not in item.name:
        return
    try:
        _report(item)
    except Exception as exc:
        print(f"DIAG report failed: {exc!r}")


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, round(p * len(s)) - 1))]


def _report(item: pytest.Item) -> None:  # noqa: PLR0915
    import psycopg  # noqa: PLC0415
    from psycopg.rows import dict_row  # noqa: PLC0415

    from tests._pg import APP, OWNER  # noqa: PLC0415

    wall = time.time() - CPU["wall_before"]
    ru = resource.getrusage(resource.RUSAGE_SELF)
    proc_cpu = (ru.ru_utime - CPU["ru_before"].ru_utime) + (ru.ru_stime - CPU["ru_before"].ru_stime)
    after = _stat()
    delta = [a - b for a, b in zip(after, CPU["stat_before"], strict=False)]
    total = sum(delta) or 1
    names = ["user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal"]
    host = {n: round(100 * d / total, 1) for n, d in zip(names, delta, strict=False)}
    print("\n=== DIAG T-01 ===")
    print(f"DIAG containers before: {CPU['ps_before']}")
    print(f"DIAG containers after:  {_docker_ps()}")
    print(f"DIAG loadavg before {CPU['load_before']} after {os.getloadavg()}")
    print(f"DIAG host cpu% over test: {host} ncpu={os.cpu_count()}")
    print(
        f"DIAG test process cpu {proc_cpu:.2f}s over wall {wall:.2f}s ({100 * proc_cpu / wall:.0f}%)"
    )
    print(
        f"DIAG loop lag ms: n={len(LAG)} p50={_pct(LAG, 0.5):.1f} p95={_pct(LAG, 0.95):.1f} "
        f"p99={_pct(LAG, 0.99):.1f} max={max(LAG, default=0):.1f} "
        f"sum>20ms={sum(x for x in LAG if x > 20):.0f}"
    )
    post_ms = [(e - s) * 1000 for s, e, t in POSTS if t.startswith("Send Acme")]
    print(
        f"DIAG POST /v1/tasks ms: n={len(post_ms)} p50={_pct(post_ms, 0.5):.0f} "
        f"p95={_pct(post_ms, 0.95):.0f} max={max(post_ms, default=0):.0f}"
    )
    post_end = {t: e for s, e, t in POSTS}
    post_start = {t: s for s, e, t in POSTS}

    db = item.funcargs["db"]
    sys_db = item._request.getfixturevalue("dbos_sys_db")  # type: ignore[attr-defined]
    with psycopg.connect(db.libpq(OWNER), row_factory=dict_row) as conn:
        tasks = conn.execute(
            "SELECT t.id::text, t.title, extract(epoch FROM t.created_at) AS c,"
            " extract(epoch FROM t.updated_at) AS u,"
            " extract(epoch FROM o.sent_at) AS sent, o.event_id::text AS ev,"
            " (SELECT extract(epoch FROM o2.sent_at) FROM outbox o2"
            "   WHERE o2.name = 'task.updated' AND o2.payload->>'task_id' = t.id::text"
            "   ORDER BY o2.created_at LIMIT 1) AS upd_sent"
            " FROM tasks t JOIN outbox o ON o.name = 'task.created'"
            "  AND o.payload->>'task_id' = t.id::text"
            " WHERE t.label IS NOT NULL"
        ).fetchall()
        counts = conn.execute(
            "SELECT name, count(*) AS n, count(sent_at) AS sent FROM outbox GROUP BY name"
        ).fetchall()
    with psycopg.connect(sys_db.libpq(APP), row_factory=dict_row) as conn:
        wfs = {
            r["workflow_uuid"]: r
            for r in conn.execute(
                "SELECT workflow_uuid, created_at, updated_at, status, queue_name FROM dbos.workflow_status"
            ).fetchall()
        }
        steps: dict[str, dict[str, tuple[int, int]]] = {}
        for r in conn.execute(
            "SELECT workflow_uuid, function_name, started_at_epoch_ms s, completed_at_epoch_ms e"
            " FROM dbos.operation_outputs"
        ).fetchall():
            steps.setdefault(r["workflow_uuid"], {})[r["function_name"].rsplit(".", 1)[-1]] = (
                r["s"],
                r["e"],
            )
        queued = conn.execute(
            "SELECT name, queue_name, count(*) n,"
            " avg(coalesce(started_at_epoch_ms, updated_at) - created_at) wait_ms,"
            " avg(updated_at - coalesce(started_at_epoch_ms, created_at)) run_ms"
            " FROM dbos.workflow_status GROUP BY 1, 2 ORDER BY 1"
        ).fetchall()
    print(f"DIAG outbox rows: {[(r['name'], r['n'], r['sent']) for r in counts]}")
    print(
        "DIAG workflows: "
        + "; ".join(
            f"{r['name']}@{r['queue_name']} n={r['n']} wait={float(r['wait_ms'] or 0):.0f}ms"
            f" run={float(r['run_ms'] or 0):.0f}ms"
            for r in queued
        )
    )
    cols = [
        "total",
        "post_in_txn",
        "commit_to_relay",
        "relay_to_wf",
        "wf_to_load",
        "load",
        "load_to_decide",
        "decide",
        "decide_to_apply",
        "apply_to_commit",
    ]
    rows = []
    for t in tasks:
        c = float(t["c"]) * 1000
        u = float(t["u"]) * 1000
        sent = float(t["sent"]) * 1000
        wf = wfs.get(f"label:{t['ev']}")
        st = steps.get(f"label:{t['ev']}", {})
        pe = post_end.get(t["title"])
        if wf is None or "load_label_task" not in st or "apply_label" not in st:
            continue
        ld, dc, ap = st["load_label_task"], st["decide_label"], st["apply_label"]
        rows.append(
            {
                "title": t["title"],
                "post_start": (post_start.get(t["title"], 0) * 1000 - c) if pe else float("nan"),
                "total": u - c,
                "post_in_txn": (pe * 1000 - c) if pe else float("nan"),
                "commit_to_relay": sent - (pe * 1000 if pe else c),
                "relay_to_wf": float(wf["created_at"]) - sent,
                "wf_to_load": ld[0] - float(wf["created_at"]),
                "load": ld[1] - ld[0],
                "load_to_decide": dc[0] - ld[1],
                "decide": dc[1] - dc[0],
                "decide_to_apply": ap[0] - dc[1],
                "apply_to_commit": u - ap[0],
            }
        )
    rows.sort(key=lambda r: r["total"])
    print("DIAG per task (ms): " + " ".join(f"{c:>9}" for c in cols) + "  post_start_vs_created")
    for r in rows:
        print(
            f"DIAG {r['title'][18:22]:>4}: "
            + " ".join(f"{r[c]:9.0f}" for c in cols)
            + f"  {r['post_start']:.0f}"
        )
    for c in cols:
        xs = [r[c] for r in rows if r[c] == r[c]]
        if xs:
            print(
                f"DIAG seg {c:>16}: p50={statistics.median(xs):7.1f} p95={_pct(xs, 0.95):7.1f}"
                f" max={max(xs):7.1f} mean={statistics.fmean(xs):7.1f}"
            )
    print("=== END DIAG ===")
