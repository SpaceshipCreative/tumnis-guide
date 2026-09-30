"""Throwaway skill cases for the harness unit tests: one recorded enrichment packet (a
Human task) and a case file pointing at it, written to tmp_path."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

TASK_ID = "01950000-0000-7000-8000-000000000601"
RUN_ID = "01950000-0000-7000-8000-000000000602"
PROFILE_ID = "01950000-0000-7000-8000-000000000603"

BODY: dict[str, Any] = {
    "schema_version": 1,
    "task": {
        "id": TASK_ID,
        "title": "Get the Acme contract signed",
        "label": "human",
        "label_reason": "Needs a signature",
        "parent_title": None,
        "due_on": "2026-03-13",
        "priority": "high",
        "first_action": None,
        "acceptance_criteria": None,
        "estimate_minutes": None,
    },
    "missing": ["first_action", "acceptance_criteria", "estimate_minutes"],
    "project": {"name": "Acme site", "client": "Acme", "goal": "Launch the new site"},
    "brief": "Acme's marketing site rebuild.",
    "passages": [],
    "estimate_history": [],
}


def packet(body: dict[str, Any] | None = None) -> dict[str, Any]:
    body = BODY if body is None else body
    return {
        "schema_version": 1,
        "kind": "enrich",
        "run_id": RUN_ID,
        "profile_id": PROFILE_ID,
        "skill": "enrich",
        "output_schema": {"family": "enrichment", "name": "result", "version": 1},
        "correlation_id": f"run:{RUN_ID}",
        "timeout_s": 120,
        "prompt_text": "Use the skill enrich.\n<packet>\n" + json.dumps(body) + "\n</packet>\n",
        "body": body,
    }


def case_doc(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "id": "enrich-unit-human",
        "profile": "project-template",
        "skill": "enrich",
        "input": "unit.packet.json",
        "output_schema": {"family": "enrichment", "name": "result", "version": 1},
        "expect": {
            "tool_calls": {"allow": []},
            "rules": ["enrichment_errors"],
            "json": [
                {"path": "$.task_id", "equals_input": "$.body.task.id"},
                {"path": "$.estimate_minutes", "type": "integer", "between": [5, 480]},
                {"path": "$.hybrid_split", "absent": True},
            ],
        },
    }
    doc.update(overrides)
    return doc


def write_case(tmp_path: Path, name: str = "case.yaml", **overrides: Any) -> Path:
    """The packet as tmp_path/unit.packet.json and the case as tmp_path/<name>."""
    (tmp_path / "unit.packet.json").write_text(json.dumps(packet()))
    path = tmp_path / name
    path.write_text(yaml.safe_dump(case_doc(**overrides), sort_keys=False))
    return path
