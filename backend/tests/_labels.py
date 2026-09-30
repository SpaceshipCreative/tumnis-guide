"""Helpers for the quick-add label tests (P1-07): they run in the decisions and tasks
integration suites, so they live here (a module's tests may not import another module's
tests). No assertions: they arrange fakes, run the relay and wait, so the code under test
may change without touching a locked test body.

- `label_answers(label, confidence)`: the `quick_add_label` answers of the recording
  `quick_add_label__hybrid.json`, with the label question's winner and confidence replaced.
- `use_label_fakes(jev, vllm=None)`: the decisions providers every `decide` in this process
  asks (the workflow runs in the `dbos` fixture's worker threads); `reset_label_fakes()`
  undoes it.
- `relay_running()`: the outbox relay listening in the background, as in the worker.
- `quiesce()`: relays until the outbox is empty and no DBOS workflow is queued or running.
- `until(check)`: polls an async check until it returns something truthy.
- `Gate`: a fake's injected `sleep` that holds the provider call until `release()`; the
  queue's workflows run on DBOS threads, so it waits on a thread event.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import psycopg

from tests._pg import OWNER, DbUrls

RECORDING = (
    Path(__file__).resolve().parents[1]
    / "tumnis/modules/decisions/tests/recordings/jev/quick_add_label__hybrid.json"
)
LABELS = ("human", "ai", "hybrid", "unknown")


def recorded_latency_ms() -> int:
    """The recording's latency (its p50), the fake's latency in the budget test."""
    return int(json.loads(RECORDING.read_text())["latency_ms"])


def label_answers(label: str, confidence: float) -> dict[str, Any]:
    """The recorded answers with `label` winning at `confidence`; the rest of the
    probability shared by the other options."""
    body = json.loads(RECORDING.read_text())["response"]["body"]["answers"]
    rest = round((1 - confidence) / (len(LABELS) - 1), 4)
    body["label"] = {
        "type": "choice",
        "choice": label,
        "confidence": confidence,
        "probabilities": {key: confidence if key == label else rest for key in LABELS},
    }
    return dict(body)


def use_label_fakes(jev: Any, vllm: Any = None) -> None:
    from tumnis.modules.decisions import api as decisions  # noqa: PLC0415

    decisions.use_providers(decisions.Providers(jev=jev, vllm=vllm))


def reset_label_fakes() -> None:
    from tumnis.modules.decisions import api as decisions  # noqa: PLC0415

    use = getattr(decisions, "use_providers", None)
    if use is not None:
        use(None)


@contextlib.asynccontextmanager
async def relay_running(poll_s: float = 1.0) -> AsyncIterator[None]:
    from tumnis.core.events import relay_forever  # noqa: PLC0415

    stop = asyncio.Event()
    task = asyncio.create_task(relay_forever(stop, poll_s=poll_s))
    await asyncio.sleep(0.3)  # connected and LISTENing
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _unsent(db: DbUrls) -> int:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        row = conn.execute(b"SELECT count(*) FROM outbox WHERE sent_at IS NULL").fetchone()
    return int(row[0]) if row else 0


async def quiesce(db: DbUrls, timeout_s: float = 30) -> None:
    """Relay and wait until nothing is left to deliver or run, twice in a row."""
    from dbos import DBOS  # noqa: PLC0415

    from tumnis.core.events import relay_once  # noqa: PLC0415

    deadline = time.monotonic() + timeout_s
    calm = 0
    while calm < 2:
        await relay_once()
        busy = await DBOS.list_workflows_async(status=["ENQUEUED", "PENDING"])
        calm = calm + 1 if not busy and _unsent(db) == 0 else 0
        if time.monotonic() > deadline:
            raise TimeoutError(f"still busy after {timeout_s} s: {[w.name for w in busy]}")
        await asyncio.sleep(0.1)


async def until[T](
    check: Callable[[], Awaitable[T]], *, timeout_s: float = 20, poll_s: float = 0.05
) -> T:
    """The first truthy value of `check`, or its last value after `timeout_s`."""
    deadline = time.monotonic() + timeout_s
    while True:
        value = await check()
        if value or time.monotonic() >= deadline:
            return value
        await asyncio.sleep(poll_s)


def owner_query(db: DbUrls, query: str, *params: Any) -> list[dict[str, Any]]:
    from psycopg.rows import dict_row  # noqa: PLC0415

    with psycopg.connect(db.libpq(OWNER), row_factory=dict_row) as conn:
        return list(conn.execute(query.encode(), params).fetchall())


class Gate:
    """A fake provider's `sleep`: the call waits until `release()`; `entered` is set once
    the call is waiting."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self._open = threading.Event()

    async def __call__(self, _seconds: float) -> None:
        self.entered.set()
        await asyncio.to_thread(self._open.wait, 30)

    def release(self) -> None:
        self._open.set()


def gated_jev(gate: Gate) -> Any:
    """A Jev fake whose calls wait on `gate` (script it with a latency above zero)."""
    from tumnis.modules.decisions.adapters.fake import FakeDecisions  # noqa: PLC0415

    return FakeDecisions(sleep=gate)
