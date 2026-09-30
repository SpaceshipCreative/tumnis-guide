"""Replay across random disconnects (P2-07, FR-5.11): every message the daemon produces
reaches the server exactly once as far as the server's store can tell, whatever happens to
the connection, the acks or the daemon process in between.

The daemon (its `StateStore` over the SQLite outbox) talks through `MemoryLink` to a
`ServerDouble` that implements the server's rule: store once per `message_id`, ack every
receipt after storing it. Hypothesis draws a run of 1 to 200 outbound messages (stream
lines, artifacts, one result at the end), a fault schedule (disconnect after message k,
drop a batch of acks, deliver a batch twice, deliver a batch reordered, restart the daemon
process by reopening its outbox) and the outbox's size cap (small caps make it drop log
lines behind a declared gap).
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.conftest import MemoryLink, ServerDouble

KINDS = ("log", "log", "log", "tool_call", "file_touched", "artifact")
FAULTS = ("disconnect", "drop_acks", "duplicate_acks", "reorder_acks", "restart")
GAP = re.compile(r"^gap: (\d+) lines \(seq (\d+)-(\d+)\)$")
ACK_EVERY = 3  # the server's batched acks reach the daemon every few messages


@st.composite
def scenarios(draw: st.DrawFn) -> tuple[list[str], dict[int, list[str]], int]:
    count = draw(st.integers(min_value=1, max_value=200))
    kinds = [*draw(st.lists(st.sampled_from(KINDS), min_size=count - 1, max_size=count - 1))]
    kinds.append("result")
    faults: dict[int, list[str]] = defaultdict(list)
    for position, fault in draw(
        st.lists(st.tuples(st.integers(0, count), st.sampled_from(FAULTS)), max_size=12)
    ):
        faults[position].append(fault)
    max_bytes = draw(st.sampled_from([50 * 1024 * 1024, 8_000, 3_000]))
    return kinds, dict(faults), max_bytes


def _message(kind: str, run_id: uuid.UUID, seq: int, index: int) -> Any:
    from tumnis_daemon.protocol import (  # noqa: PLC0415
        ResultV2,
        envelope,
        make_artifact,
        make_stream,
    )

    corr = f"run:{run_id}"
    if kind == "artifact":
        return make_artifact(run_id, corr, f"notes-{index}.md", "text/markdown", f"# {index}\n")
    if kind == "result":
        return ResultV2(
            **envelope(corr),
            run_id=run_id,
            status="succeeded",
            exit_code=0,
            output_json={"done": index},
            text="{}",
            duration_ms=index,
        )
    return make_stream(run_id, corr, seq, kind, f"{kind} line {seq} " + "z" * 120)


async def _play(  # noqa: PLR0912, PLR0915  # one scenario, read top to bottom
    root: Path, kinds: list[str], faults: dict[int, list[str]], cap: int
) -> None:
    from tumnis_daemon.state import StateStore  # noqa: PLC0415

    server = ServerDouble()
    run_id = uuid.uuid4()

    def open_state() -> StateStore:
        opened = StateStore(root, outbox_max_bytes=cap)
        opened.protocol_version = 2
        return opened

    state = open_state()
    link: MemoryLink | None = None

    async def dial() -> MemoryLink:
        fresh = MemoryLink(server)
        state.ws = fresh
        await state.replay_unacked(fresh)
        return fresh

    def hang_up() -> None:
        nonlocal link
        if link is not None:
            link.close()
        link = None
        state.ws = None
        server.take_acks()  # acks in flight die with the connection

    produced: list[dict[str, Any]] = []
    seq = 0
    for index, kind in enumerate(kinds):
        for fault in faults.get(index, []):
            if fault == "disconnect":
                hang_up()
            elif fault == "restart":
                hang_up()
                state.close()
                state = open_state()
            elif fault == "drop_acks":
                server.take_acks()
            elif fault == "duplicate_acks":
                acks = server.take_acks()
                state.acked(acks)
                state.acked(acks)
            elif fault == "reorder_acks":
                state.acked(list(reversed(server.take_acks())))
        if link is None:
            link = await dial()
        if kind in ("log", "tool_call", "file_touched"):
            seq += 1
        message = _message(kind, run_id, seq, index)
        produced.append(json.loads(message.model_dump_json()))
        await state.send_reliably(message)
        if index % ACK_EVERY == ACK_EVERY - 1:
            state.acked(server.take_acks())

    # A quiet end: the connection comes back until every message is acked.
    for _ in range(4):
        state.acked(server.take_acks())
        if not state.unacked():
            break
        hang_up()
        link = await dial()
        state.acked(server.take_acks())

    # The outbox is empty.
    assert state.unacked() == []
    state.close()

    stored = server.stored
    # Every result, tool call, touched file and artifact reached the server; the result
    # exactly once.
    for message in produced:
        if message["type"] != "stream" or message["kind"] != "log":
            assert message["message_id"] in stored, f"lost a {message['type']}"
    results = [m for m in stored.values() if m["type"] == "result"]
    assert len(results) == 1
    # Nothing the daemon never produced (bar gap markers) was stored.
    produced_ids = {m["message_id"] for m in produced}
    extra = [m for m in stored.values() if m["message_id"] not in produced_ids]
    assert all(m["type"] == "status" and m["state"] == "gap" for m in extra)
    # Stream lines by seq have no holes except declared gaps.
    declared: set[int] = set()
    for gap in extra:
        found = GAP.match(gap["detail"])
        assert found is not None, gap["detail"]
        declared |= set(range(int(found.group(2)), int(found.group(3)) + 1))
    seqs = {m["seq"] for m in stored.values() if m["type"] == "stream"}
    holes = set(range(1, seq + 1)) - seqs
    assert holes <= declared, f"undeclared holes: {sorted(holes - declared)[:10]}"
    # A message may arrive more than once; the store holds it once.
    assert all(server.receipts[m] >= 1 for m in stored)


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P2-07")
@given(scenarios())
def test_random_disconnects_deliver_each_message_once(
    scenario: tuple[list[str], dict[int, list[str]], int],
) -> None:
    """T-P2-07-03
    For any run of 1 to 200 messages and any schedule of disconnects, dropped, duplicated
    and reordered ack batches and daemon restarts: the server stored each `message_id`
    once, every non-log message arrived, the result exactly once, stream lines ordered by
    `seq` have no holes except declared gaps, and the daemon's outbox is empty at the end.
    """
    kinds, faults, cap = scenario
    with tempfile.TemporaryDirectory() as root:
        asyncio.run(_play(Path(root), kinds, faults, cap))
