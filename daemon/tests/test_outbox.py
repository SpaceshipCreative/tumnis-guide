"""The daemon outbox (P2-07, FR-5.11): durable before a send is attempted; when full, the
oldest `log` stream lines give way to one `gap` status, and nothing else is ever dropped."""

from __future__ import annotations

import json
import re
import uuid
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

GAP = re.compile(r"^gap: (\d+) lines \(seq (\d+)-(\d+)\)$")


def _size(message: dict[str, Any]) -> int:
    return len(json.dumps(message).encode())


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P2-07")
@pytest.mark.xfail(strict=True, reason="spec:P2-07")
def test_outbox_full_drops_log_lines_never_results(tmp_path: Path) -> None:
    """T-P2-07-14
    Past `max_bytes`, the oldest `log` lines are dropped and replaced, where the first of
    them stood, by one `status` message with state `gap` naming how many lines went and
    their seq range; `tool_call` and `file_touched` lines, artifacts and the result are all
    kept, in order, and survive a reopen of the outbox.
    """
    from tumnis_daemon.outbox import Outbox  # noqa: PLC0415
    from tumnis_daemon.protocol import (  # noqa: PLC0415
        ResultV2,
        envelope,
        make_artifact,
        make_stream,
    )

    run_id = uuid.uuid4()
    corr = f"run:{run_id}"
    path = tmp_path / "state.db"
    box = Outbox(path, max_bytes=6_000)

    def put(message: Any) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(message.model_dump_json())
        box.put(data)
        return data

    logs = [put(make_stream(run_id, corr, seq, "log", "x" * 200)) for seq in range(1, 31)]
    tool = put(make_stream(run_id, corr, 31, "tool_call", '{"name": "skill_view"}'))
    touched = put(make_stream(run_id, corr, 32, "file_touched", "src/app.py"))
    artifact = put(make_artifact(run_id, corr, "notes.md", "text/markdown", "# Notes\n" * 50))
    late = [put(make_stream(run_id, corr, seq, "log", "y" * 200)) for seq in range(33, 41)]
    result = put(
        ResultV2(
            **envelope(corr),
            run_id=run_id,
            status="succeeded",
            exit_code=0,
            output_json={"ok": True},
            text='{"ok": true}',
            duration_ms=10,
        )
    )

    for reopened in (False, True):
        if reopened:
            box.close()
            box = Outbox(path, max_bytes=6_000)
        kept = list(box.unacked())
        ids = [m["message_id"] for m in kept]
        for must in (tool, touched, artifact, result):
            assert must["message_id"] in ids, f"{must['type']} was dropped"
        assert sum(1 for m in kept if m["type"] == "result") == 1

        gaps = [m for m in kept if m["type"] == "status" and m["state"] == "gap"]
        assert gaps, "no gap marker"
        dropped_seqs: set[int] = set()
        for gap in gaps:
            assert gap["run_id"] == str(run_id)
            found = GAP.match(gap["detail"])
            assert found is not None, gap["detail"]
            count, first, last = (int(g) for g in found.groups())
            assert 1 <= count <= last - first + 1
            dropped_seqs |= {s for s in range(first, last + 1) if s <= 40}
        kept_logs = [m["seq"] for m in kept if m["type"] == "stream" and m["kind"] == "log"]
        all_logs = [m["seq"] for m in logs + late]
        lost = set(all_logs) - set(kept_logs)
        assert lost, "nothing was dropped although the outbox is full"
        assert lost <= dropped_seqs  # every missing line is declared
        assert max(lost) < min(kept_logs)  # the oldest went first
        assert sum(int(GAP.match(g["detail"]).group(1)) for g in gaps) == len(lost)  # type: ignore[union-attr]
        # The gap stands where the dropped lines stood: before every line still kept.
        first_gap = ids.index(gaps[0]["message_id"])
        first_kept_log = min(i for i, m in enumerate(kept) if m.get("kind") == "log")
        assert first_gap < first_kept_log
        # Order is insertion order otherwise.
        assert ids.index(tool["message_id"]) < ids.index(artifact["message_id"])
        assert ids.index(artifact["message_id"]) < ids.index(result["message_id"])
        assert sum(_size(m) for m in kept) <= 6_000

    box.ack([result["message_id"], tool["message_id"]])
    assert result["message_id"] not in [m["message_id"] for m in box.unacked()]
    box.close()
