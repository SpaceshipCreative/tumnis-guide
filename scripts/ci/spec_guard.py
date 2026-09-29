#!/usr/bin/env python3
"""spec-guard: fail a PR that weakens a locked test (AGENTS.md TDD rule 4, P0-03).

Compares every test file changed between --base and --head. Deleting a test file or a
test, editing a test's body, or adding a skip or non-spec xfail is a violation. Removing
a `spec:` xfail marker is allowed. Only the `spec-change` label added by an owner (repo
variable SPEC_CHANGE_ACTORS) waives violations; the report is printed either way.

    python scripts/ci/spec_guard.py --base origin/main --head HEAD --labels ""
"""

from __future__ import annotations

import argparse
import ast
import difflib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _tests_extract import (
    Change,
    changes,
    dump,
    is_python_test,
    locked_blocks,
    show,
)


@dataclass(frozen=True)
class Violation:
    path: str
    test: str
    kind: str  # deleted_file | deleted_test | edited_test | added_skip_or_xfail | ...
    detail: str


def unified_diff(before: str, after: str) -> str:
    lines = difflib.unified_diff(
        before.splitlines(), after.splitlines(), "base", "head", lineterm="", n=1
    )
    return "\n".join(lines)


def normalize(node: ast.AST) -> str:
    return dump(node)


def compare_python(path: str, base_src: str, head_src: str) -> list[Violation]:
    base, head = locked_blocks(base_src), locked_blocks(head_src)
    out: list[Violation] = []
    for name, b in base.items():
        h = head.get(name)
        if h is None:
            out.append(Violation(path, name, "deleted_test", "present on base, missing on head"))
            continue
        if normalize(b) != normalize(h):
            out.append(
                Violation(path, name, "edited_test", unified_diff(ast.unparse(b), ast.unparse(h)))
            )
    return out


def _compare(repo: Path, base: str, head: str, change: Change) -> list[Violation]:
    assert change.old is not None  # noqa: S101 (only called for M, D and R)
    base_src = show(repo, base, change.old)
    if base_src is None:
        return []
    if change.status == "D":
        return [Violation(change.old, "*", "deleted_file", "test file deleted")]
    assert change.new is not None  # noqa: S101
    head_src = show(repo, head, change.new) or ""
    if is_python_test(change.old):
        return compare_python(change.new, base_src, head_src)
    return []


def collect(repo: Path, base: str, head: str) -> list[Violation]:
    """Every violation between base and head, from `git diff --name-status -M base...head`."""
    out: list[Violation] = []
    for change in changes(repo, base, head):
        if change.status == "A" or change.old is None or not is_python_test(change.old):
            continue
        out.extend(_compare(repo, base, head, change))
    return out


def report(violations: list[Violation], *, waived: bool) -> str:
    if not violations:
        return "## spec-guard\n\nNo locked test was weakened.\n"
    heading = "waived by the `spec-change` label" if waived else "blocking"
    lines = [f"## spec-guard: {len(violations)} violation(s), {heading}", ""]
    for v in violations:
        lines += [f"- `{v.path}` :: `{v.test}`: **{v.kind}**"]
        if v.detail:
            lines += ["", "  ```diff", *(f"  {line}" for line in v.detail.splitlines()), "  ```"]
    return "\n".join(lines) + "\n"


def print_report(violations: list[Violation], *, waived: bool) -> None:
    text = report(violations, waived=waived)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a") as handle:
            handle.write(text)


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--labels", default="", help="comma-separated PR labels")
    parser.add_argument("--pr", default="", help="PR number, to check who added a label")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse(argv)
    violations = collect(Path(args.repo), args.base, args.head)
    print_report(violations, waived=False)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
