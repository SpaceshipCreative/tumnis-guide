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
import copy
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
    is_spec_xfail,
    is_test_file,
    is_ts_test,
    is_weakening_decorator,
    locked_blocks,
    matches_any,
    show,
    ts_blocks,
)

EXEMPT_GLOBS = ("backend/tests/contract/generated/**", "frontend/src/api/**")
GENERATED_HEADER = ("# @generated", "// @generated")
# Guard and CI definitions are locked files: an agent's PR may not loosen its own checks.
LOCKED_GLOBS = ("scripts/ci/**", ".github/**")
PYTESTMARK = "<pytestmark>"


@dataclass(frozen=True)
class Violation:
    path: str
    test: str
    kind: str  # deleted_file | deleted_test | edited_test | added_skip_or_xfail
    #            | edited_pytestmark | edited_locked_file
    detail: str


def unified_diff(before: str, after: str) -> str:
    lines = difflib.unified_diff(
        before.splitlines(), after.splitlines(), "base", "head", lineterm="", n=1
    )
    return "\n".join(lines)


def normalize(node: ast.AST) -> str:
    """Drop spec xfail decorators (and spec xfail entries in pytestmark), then dump."""
    node = copy.deepcopy(node)
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        node.decorator_list = [d for d in node.decorator_list if not is_spec_xfail(d)]
    if isinstance(node, ast.Assign):
        if isinstance(node.value, ast.List | ast.Tuple):
            node.value.elts = [e for e in node.value.elts if not is_spec_xfail(e)]
        elif is_spec_xfail(node.value):
            node.value = ast.List(elts=[], ctx=ast.Load())
    return dump(node)


def _marks(node: ast.AST) -> list[ast.expr]:
    """The markers a pytestmark assignment keeps once spec xfails are dropped."""
    value = getattr(node, "value", None)
    if isinstance(value, ast.List | ast.Tuple):
        return [e for e in value.elts if not is_spec_xfail(e)]
    return [] if value is None or is_spec_xfail(value) else [value]


def _added_weakening(base: ast.AST, head: ast.AST) -> list[ast.expr]:
    """Skip, skipif or non-spec xfail decorators on head that base did not have."""
    if not isinstance(head, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return []
    before = {dump(d) for d in getattr(base, "decorator_list", [])}
    return [d for d in head.decorator_list if is_weakening_decorator(d) and dump(d) not in before]


def compare_python(path: str, base_src: str, head_src: str) -> list[Violation]:
    base, head = locked_blocks(base_src), locked_blocks(head_src)
    out: list[Violation] = []
    for name, b in base.items():
        h = head.get(name)
        if h is None and name == PYTESTMARK and not _marks(b):
            continue  # the line held only spec xfail markers
        if h is None:
            out.append(Violation(path, name, "deleted_test", "present on base, missing on head"))
            continue
        added = _added_weakening(b, h)
        if added:
            out.append(Violation(path, name, "added_skip_or_xfail", ast.unparse(added[0])))
            continue
        if normalize(b) != normalize(h):
            kind = "edited_pytestmark" if name == PYTESTMARK else "edited_test"
            out.append(Violation(path, name, kind, unified_diff(ast.unparse(b), ast.unparse(h))))
    return out


def compare_typescript(path: str, base_src: str, head_src: str) -> list[Violation]:
    """Same rules on Vitest and Playwright blocks; fails-to-test flips are allowed."""
    blocks = ts_blocks({"base/" + path: base_src, "head/" + path: head_src})
    base, head = blocks["base/" + path], blocks["head/" + path]
    out: list[Violation] = []
    for key, b in base.items():
        h = head.get(key)
        if h is None:
            out.append(Violation(path, key, "deleted_test", "present on base, missing on head"))
        elif (h["skip"] and not b["skip"]) or (h["fails"] and not b["fails"]):
            out.append(Violation(path, key, "added_skip_or_xfail", f"line {h['line']}"))
        elif h["text"] != b["text"]:
            out.append(Violation(path, key, "edited_test", unified_diff(b["text"], h["text"])))
    return out


def is_exempt(path: str, base_src: str) -> bool:
    """Generated tests: under an exempt path, or generated on the base commit."""
    first = base_src.split("\n", 1)[0].strip()
    return matches_any(path, EXEMPT_GLOBS) or first.startswith(GENERATED_HEADER)


def _compare_text(path: str, base_src: str, head_src: str) -> list[Violation]:
    """Skill test cases and other data-only tests: any change but whitespace is an edit."""
    before = [line.rstrip() for line in base_src.strip().splitlines()]
    after = [line.rstrip() for line in head_src.strip().splitlines()]
    if before == after:
        return []
    return [Violation(path, "*", "edited_test", unified_diff(base_src, head_src))]


def _compare(repo: Path, base: str, head: str, change: Change) -> list[Violation]:
    assert change.old is not None  # noqa: S101 (only called for M, D and R)
    base_src = show(repo, base, change.old)
    if base_src is None or is_exempt(change.old, base_src):
        return []
    if change.status == "D":
        return [Violation(change.old, "*", "deleted_file", "test file deleted")]
    assert change.new is not None  # noqa: S101
    if not is_test_file(change.new):
        return [Violation(change.old, "*", "deleted_file", f"renamed to {change.new}")]
    return compare_file(change.new, base_src, show(repo, head, change.new) or "")


def compare_file(path: str, base_src: str, head_src: str) -> list[Violation]:
    if is_python_test(path):
        try:
            return compare_python(path, base_src, head_src)
        except SyntaxError as error:
            return [Violation(path, "*", "edited_test", f"head does not parse: {error}")]
    if is_ts_test(path):
        return compare_typescript(path, base_src, head_src)
    return _compare_text(path, base_src, head_src)


def collect(repo: Path, base: str, head: str) -> list[Violation]:
    """Every violation between base and head, from `git diff --name-status -M base...head`.

    A (added) is never a violation; D (deleted) is deleted_file; R (renamed) compares the
    old path's blocks with the new path's; M compares blocks. Exempt paths and generated
    files are skipped. Any change to an existing file under LOCKED_GLOBS is a violation.
    """
    out: list[Violation] = []
    for change in changes(repo, base, head):
        if change.status == "A" or change.old is None:
            continue
        if matches_any(change.old, LOCKED_GLOBS):
            detail = "CI and guard files change only with the spec-change label"
            out.append(Violation(change.old, "*", "edited_locked_file", detail))
        elif is_test_file(change.old):
            out.extend(_compare(repo, base, head, change))
    return out


def warnings(repo: Path, base: str, head: str) -> list[str]:
    """Non-blocking: changed fixtures could weaken a test without touching it."""
    return sorted(
        change.old
        for change in changes(repo, base, head)
        if change.old and change.status != "A" and change.old.endswith("conftest.py")
    )


def report(violations: list[Violation], *, waived: bool, changed_fixtures: list[str]) -> str:
    if violations:
        state = "waived by the `spec-change` label" if waived else "blocking"
        lines = [f"## spec-guard: {len(violations)} violation(s), {state}", ""]
    else:
        lines = ["## spec-guard", "", "No locked test was weakened."]
    for v in violations:
        if "\n" not in v.detail:
            lines += [f"- `{v.path}` :: `{v.test}`: **{v.kind}** ({v.detail})"]
            continue
        lines += [f"- `{v.path}` :: `{v.test}`: **{v.kind}**", "", "  ```diff"]
        lines += [*(f"  {line}" for line in v.detail.splitlines()), "  ```"]
    if changed_fixtures:
        lines += ["", "Warning (not blocking): changed fixtures; check they do not weaken tests:"]
        lines += [f"- `{path}`" for path in changed_fixtures]
    return "\n".join(lines) + "\n"


def print_report(text: str) -> None:
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
    repo = Path(args.repo)
    violations = collect(repo, args.base, args.head)
    fixtures = warnings(repo, args.base, args.head)
    print_report(report(violations, waived=False, changed_fixtures=fixtures))
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
