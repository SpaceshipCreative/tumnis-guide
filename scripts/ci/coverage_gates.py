#!/usr/bin/env python3
"""Coverage gates on `coverage json` output (ARCHITECTURE.md rule 2, P0-03).

- rules_and_mcp: every tumnis/modules/*/rules*.py and */mcp.py together, at least 80% of
  lines.
- full: planner, focus, threshold and state-machine rules (planning, focus, decisions and
  tasks rules.py), each at 100% of lines.

A group whose files have no statements yet passes.

    uv run --project backend python scripts/ci/coverage_gates.py backend/coverage.json
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MODULE = r"(?:^|/)tumnis/modules/"
RULES_AND_MCP = re.compile(MODULE + r"[^/]+/(?:rules[^/]*|mcp)\.py$")
FULL = re.compile(MODULE + r"(?:tasks|planning|focus|decisions)/rules\.py$")


@dataclass(frozen=True)
class Gate:
    name: str
    pattern: re.Pattern[str]
    minimum: float  # percent of lines
    per_file: bool


GATES = (
    Gate("rules_and_mcp", RULES_AND_MCP, 80.0, per_file=False),
    Gate("full", FULL, 100.0, per_file=True),
)


def _lines(summary: dict[str, Any]) -> tuple[int, int]:
    return int(summary.get("covered_lines", 0)), int(summary.get("num_statements", 0))


def _percent(covered: int, statements: int) -> float:
    return 100.0 if statements == 0 else 100.0 * covered / statements


def evaluate(report: dict[str, Any]) -> list[str]:
    """One message per failed gate or file; empty when every gate holds."""
    files: dict[str, dict[str, Any]] = report.get("files", {})
    failures: list[str] = []
    for gate in GATES:
        matched = {
            path: _lines(data["summary"])
            for path, data in files.items()
            if gate.pattern.search(path.replace("\\", "/"))
        }
        if gate.per_file:
            for path, (covered, statements) in sorted(matched.items()):
                if _percent(covered, statements) < gate.minimum:
                    failures.append(
                        f"{gate.name}: {path} covers {covered}/{statements} lines, "
                        f"needs {gate.minimum:.0f}%"
                    )
            continue
        covered = sum(c for c, _ in matched.values())
        statements = sum(s for _, s in matched.values())
        if _percent(covered, statements) < gate.minimum:
            failures.append(
                f"{gate.name}: {covered}/{statements} lines "
                f"({_percent(covered, statements):.1f}%), needs {gate.minimum:.0f}%"
            )
    return failures


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: coverage_gates.py <coverage.json>", file=sys.stderr)
        return 2
    failures = evaluate(json.loads(Path(argv[0]).read_text()))
    text = "## Coverage gates\n\n" + (
        "\n".join(f"- {f}" for f in failures) if failures else "All gates hold."
    )
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a") as handle:
            handle.write(text + "\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
