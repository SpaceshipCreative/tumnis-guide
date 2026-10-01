"""The full mock Tumnis MCP server for skill cases (P2-12).

It extends the minimal recording mock (harness.mock_mcp_min): it serves every tool of the
committed catalogue (schemas/mcp/v1/tools.json) with its input schema, plus a stub for
each tool the PRD names that a later work package brings (agent_surface's PENDING_TOOLS,
e.g. delegate_task and wait_for_task from P2-06), so a tool schema change breaks the skill
cases at once rather than in production. It checks every call's arguments against the
tool's input schema (a call that does not validate is answered as a tool error, as the real
server would refuse it) and answers from the case's script:

- `{from: seed}` (the default for every tool): a plausible answer built from the call,
  e.g. `create_task` echoes the task with a new id, `request_approval` is `pending`;
- `{default: <status>}`: an approval or question answered with that status;
- `{result: {...}}`: exactly that object.

A tool's script is a list answered call by call; its last entry repeats. Every call is
recorded (server `tumnis`) like the minimal mock's, one JSON line per call.

    python -m harness.mock_mcp --script case-script.json --record calls.jsonl   # stdio

The script file is `{"responses": {<tool>: [<response>, ...]}, "recall": [...]}` (the
case's `mock` block; `recall` is for harness.mock_memory).
"""

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from threading import Lock
from typing import Any, Final
from uuid import NAMESPACE_URL, uuid5

import anyio
from jsonschema import Draft202012Validator
from mcp.server import Server

from harness.calls import pending_tools
from harness.mock_mcp_min import (
    CATALOGUE,
    TUMNIS,
    Check,
    Recorder,
    build_server,
    load_catalogue,
    serve_stdio,
)

MOCK_TIME: Final = "2026-03-09T12:00:00Z"
APPROVALS: Final = frozenset({"request_approval", "ask_human"})
SEED: Final[Mapping[str, Any]] = {"from": "seed"}


def full_catalogue(catalogue: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The catalogue's tools, then a stub for each pending tool it does not have yet."""
    tools = [dict(tool) for tool in catalogue]
    have = {str(tool["name"]) for tool in tools}
    tools += [
        {
            "name": name,
            "description": f"{name} (served by the mock until it reaches the catalogue).",
            "input_schema": {"type": "object"},
        }
        for name in sorted(pending_tools() - have)
    ]
    return tools


def mock_id(tool: str, n: int) -> str:
    """A stable id for the mock's n-th answer of `tool`."""
    return str(uuid5(NAMESPACE_URL, f"tumnis:mock:{tool}:{n}"))


def _no_key(arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in arguments.items() if k != "idempotency_key"}


def seed_response(tool: str, arguments: Mapping[str, Any], n: int) -> dict[str, Any]:  # noqa: PLR0911
    """The seed answer to the n-th call of `tool` (from 1)."""
    ident = mock_id(tool, n)
    if tool == "create_task":
        return {
            "schema_version": 1,
            "id": ident,
            "parent_id": None,
            "label": None,
            "status": "backlog",
            "priority": "normal",
            "estimate_minutes": None,
            "first_action": None,
            "acceptance_criteria": None,
            **_no_key(arguments),
            "version": 1,
            "tainted": False,
            "created_at": MOCK_TIME,
            "updated_at": MOCK_TIME,
        }
    if tool == "post_result":
        return {
            "schema_version": 1,
            "id": ident,
            "task_id": mock_id("task", 0),
            "created_at": MOCK_TIME,
            "tainted": False,
            **_no_key(arguments),
        }
    if tool in APPROVALS:
        return approval_response(tool, "pending", n)
    if tool in {"get_project_digest", "get_workspace_digest"}:
        scope = "project" if tool == "get_project_digest" else "workspace"
        return {
            "schema_version": 1,
            "scope": scope,
            "entries": [],
            "next_cursor": arguments.get("since"),
            "has_more": False,
        }
    if tool == "list_tasks":
        return {"items": [], "next_cursor": None, "total": 0}
    if tool == "delegate_task":
        return {"schema_version": 1, "id": ident, "status": "queued"}
    if tool == "wait_for_task":
        return {"schema_version": 1, "status": "running"}
    return {"ok": True, "mock": True}


def approval_response(tool: str, status: str, n: int) -> dict[str, Any]:
    """An approval (or question) answered with `status`, as the real server answers."""
    answer: dict[str, Any] = {"schema_version": 1, "id": mock_id(tool, n), "status": status}
    if status == "pending":
        answer["retry_after_seconds"] = 60
    return answer


class Script:
    """The case's scripted answers; thread-safe, counting each tool's calls."""

    def __init__(self, responses: Mapping[str, Sequence[Mapping[str, Any]]] | None = None):
        self.responses = {tool: list(specs) for tool, specs in (responses or {}).items()}
        self._calls: dict[str, int] = {}
        self._lock = Lock()

    @classmethod
    def load(cls, path: Path | None) -> "Script":
        if path is None:
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(data.get("responses") or {})

    def respond(self, tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            n = self._calls[tool] = self._calls.get(tool, 0) + 1
        specs = self.responses.get(tool) or [SEED]
        spec = specs[min(n, len(specs)) - 1]
        if "result" in spec:
            return dict(spec["result"])
        if "default" in spec:
            return approval_response(tool, str(spec["default"]), n)
        return seed_response(tool, arguments, n)


def argument_check(catalogue: Sequence[Mapping[str, Any]]) -> Check:
    """A `check` for build_server: the call's arguments against the tool's input schema."""
    validators = {
        str(tool["name"]): Draft202012Validator(
            dict(tool.get("input_schema") or {"type": "object"}),
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        )
        for tool in catalogue
    }

    def check(tool: str, arguments: Mapping[str, Any]) -> list[str]:
        found = validators.get(tool)
        if found is None:
            return []
        errors = sorted(found.iter_errors(dict(arguments)), key=lambda e: e.json_path)
        return [f"{e.json_path}: {e.message}" for e in errors]

    return check


def build_full_server(
    catalogue: Sequence[Mapping[str, Any]], recorder: Recorder, script: Script
) -> Server[Any]:
    """The full mock: every catalogue and pending tool, arguments checked, answers from
    `script`, every call recorded."""
    tools = full_catalogue(catalogue)
    return build_server(
        tools, recorder, name=TUMNIS, respond=script.respond, check=argument_check(tools)
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m harness.mock_mcp", description=__doc__)
    parser.add_argument("--record", type=Path, help="append every call here as a JSON line")
    parser.add_argument("--script", type=Path, help="the case's scripted answers (JSON)")
    parser.add_argument("--catalogue", type=Path, default=CATALOGUE)
    args = parser.parse_args(argv)
    server = build_full_server(
        load_catalogue(args.catalogue), Recorder(args.record), Script.load(args.script)
    )
    anyio.run(serve_stdio, server)


if __name__ == "__main__":
    main(sys.argv[1:])
