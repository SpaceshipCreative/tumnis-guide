"""Upgrading from the P1-04 daemon (P2-07, FR-5.11, REL-4): results and health reports it
left unacked in `<state_dir>/unacked/*.json` move into the outbox at start and are replayed,
so an upgrade loses nothing."""

from __future__ import annotations

import json
import os
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from tumnis_daemon.state import StateStore

if TYPE_CHECKING:
    from pathlib import Path

T = "2026-03-09T12:00:00Z"


def _legacy(unacked: Path, message: dict[str, Any], mtime_ns: int) -> Path:
    path = unacked / f"{message['message_id']}.json"
    path.write_text(json.dumps(message), encoding="utf-8")
    os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P2-07")
def test_legacy_unacked_files_move_into_the_outbox(tmp_path: Path) -> None:
    unacked = tmp_path / "unacked"
    unacked.mkdir()
    run_id = str(uuid.uuid4())
    result = {
        "schema_version": 1,
        "message_id": str(uuid.uuid4()),
        "correlation_id": f"run:{run_id}",
        "sent_at": T,
        "type": "result",
        "run_id": run_id,
        "status": "succeeded",
        "exit_code": 0,
        "output_json": {"estimate_minutes": 20},
        "text": '{"estimate_minutes": 20}',
        "duration_ms": 1000,
    }
    report = {
        "schema_version": 1,
        "message_id": str(uuid.uuid4()),
        "correlation_id": "req:1",
        "sent_at": T,
        "type": "health_report",
        "request_id": str(uuid.uuid4()),
        "profile": "acme-site",
        "ok": True,
    }
    _legacy(unacked, report, 2_000_000_000)
    _legacy(unacked, result, 1_000_000_000)
    (unacked / "broken.json").write_text("{not json", encoding="utf-8")

    store = StateStore(tmp_path)
    kept = [json.loads(frame) for frame in store.unacked()]
    assert kept == [result, report]  # oldest first, byte-for-byte the same messages
    assert sorted(p.name for p in unacked.iterdir()) == ["broken.json"]  # kept for a human
    store.close()

    again = StateStore(tmp_path)  # a second start imports nothing twice
    assert [json.loads(frame)["message_id"] for frame in again.unacked()] == [
        result["message_id"],
        report["message_id"],
    ]
    again.close()
