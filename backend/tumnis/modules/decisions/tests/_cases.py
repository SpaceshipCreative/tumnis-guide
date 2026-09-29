"""Shared test data for the decisions tests (P1-01): the nine FR-11.4 decision points and
the reviewed inputs stored beside the outbound snapshots."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

TESTS = Path(__file__).resolve().parent
SNAPSHOTS = TESTS / "snapshots" / "outbound"
RECORDINGS = TESTS / "recordings" / "jev"
PINNED_MODEL = "jev-1.13.0"

# FR-11.4's decision points and the primitive each one asks (the catalogue's stable ids).
POINTS: dict[str, str] = {
    "quick_add_label": "choice",
    "project_match": "choice",
    "actionability": "noul",
    "duplicate": "noul",
    "approval_need": "noul",
    "blocking_impact": "score",
    "focus_on_task": "noul",
    "nudge_warranted": "noul",
    "estimate_plausibility": "score",
}


def stored_inputs(point: str) -> dict[str, Any]:
    """The reviewed inputs for `point` (tests/snapshots/outbound/<point>.inputs.json)."""
    data: dict[str, Any] = json.loads((SNAPSHOTS / f"{point}.inputs.json").read_text())
    return data


def load_jev_recordings() -> list[tuple[str, dict[str, Any]]]:
    """(file name, recording) for every tests/recordings/jev/*.json, sorted by name. Each
    recording is {point, inputs, request, response: {status, headers, body}, latency_ms,
    recorded_at, sdk_version, notes}."""
    return [(path.name, json.loads(path.read_text())) for path in sorted(RECORDINGS.glob("*.json"))]


def answered_recordings() -> list[tuple[str, dict[str, Any]]]:
    """The recordings whose response is a 200 answer."""
    return [(name, rec) for name, rec in load_jev_recordings() if rec["response"]["status"] == 200]


# --- Generation slot (P1-03) ------------------------------------------------------------

GENERATION_RECORDINGS = TESTS / "recordings" / "vllm_generation"
GENERATION_BASE_URL = "http://vllm.example.org:8000"
GENERATION_MODEL = "Qwen/Qwen2.5-3B-Instruct"


def load_generation_recordings() -> list[tuple[str, dict[str, Any]]]:
    """(file name, recording) for every tests/recordings/vllm_generation/*.json, sorted by
    name. Each is {input: {system, user, max_tokens}, request: {method, url, body},
    response: {status, headers, body}, recorded_at, vllm_version, notes}."""
    return [
        (path.name, json.loads(path.read_text()))
        for path in sorted(GENERATION_RECORDINGS.glob("*.json"))
    ]
