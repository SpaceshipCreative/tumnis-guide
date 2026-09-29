#!/usr/bin/env python3
"""red-proof: a `bug` PR's new test must fail on the base commit (AGENTS.md TDD rule 5).

Finds the tests the PR adds, requires one named for an issue it fixes
(`test_issue_<n>_...`; for Vitest, a title with `issue <n>` or `#<n>`), then checks out
the base commit in a scratch worktree, copies in the head version of the changed test
files and conftests only (never source), and runs the new tests there. Any new test that
passes on base, or does not run at all, fails the job.

    python scripts/ci/red_proof.py --base <sha> --head <sha> --pr 12 --issues 42
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _tests_extract import (
    NewTest,
    added_python_tests,
    changes,
    git,
    is_ts_test,
    show,
    ts_blocks,
)

ISSUE_TEST = re.compile(r"test_issue_(\d+)_\w+")
TS_ISSUE_TEST = re.compile(r"(?:\bissue[ _-]?#?|#)(\d+)\b", re.IGNORECASE)
CLOSING = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)", re.IGNORECASE)
PROJECT_MARKERS = ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini")


@dataclass(frozen=True)
class Result:
    test: str
    outcome: str  # passed | failed | error | skipped


def fail(message: str) -> int:
    print(f"red-proof: {message}")
    _summary(f"## red-proof\n\n{message}\n")
    return 1


def _summary(text: str) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a") as handle:
            handle.write(text)


def added_tests(repo: Path, base: str, head: str) -> list[NewTest]:
    """New Python test functions and new Vitest/Playwright tests between base and head."""
    found = added_python_tests(repo, base, head)
    for change in changes(repo, base, head):
        if change.new is None or not is_ts_test(change.new):
            continue
        head_src = show(repo, head, change.new) or ""
        base_src = (show(repo, base, change.old) if change.old else None) or ""
        blocks = ts_blocks({"head/" + change.new: head_src, "base/" + change.new: base_src})
        new_keys = set(blocks["head/" + change.new]) - set(blocks["base/" + change.new])
        found.extend(NewTest(change.new, key) for key in sorted(new_keys))
    return found


def is_issue_named(test: NewTest, issues: set[str]) -> bool:
    if is_ts_test(test.path):
        return any(m.group(1) in issues for m in TS_ISSUE_TEST.finditer(test.name))
    match = ISSUE_TEST.search(test.name)
    return match is not None and match.group(1) in issues


def changed_conftests(repo: Path, base: str, head: str) -> set[str]:
    return {
        change.new
        for change in changes(repo, base, head)
        if change.new and PurePosixPath(change.new).name == "conftest.py"
    }


@contextmanager
def worktree(repo: Path, sha: str) -> Iterator[Path]:
    """A detached scratch worktree at `sha`, removed afterwards."""
    scratch = Path(tempfile.mkdtemp(prefix="red-proof-"))
    path = scratch / "base"
    git(repo, "worktree", "add", "--detach", "--quiet", str(path), sha)
    try:
        yield path
    finally:
        git(repo, "worktree", "remove", "--force", str(path), check=False)
        shutil.rmtree(scratch, ignore_errors=True)


def copy_head_version(repo: Path, head: str, path: str, base_dir: Path) -> None:
    content = show(repo, head, path)
    if content is None:
        return
    target = base_dir / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def _run_dir(base_dir: Path, path: str) -> Path:
    """The nearest folder holding the pytest configuration (backend/ in this repo)."""
    folder = (base_dir / path).parent
    while folder != base_dir and folder.is_relative_to(base_dir):
        if any((folder / marker).is_file() for marker in PROJECT_MARKERS):
            return folder
        folder = folder.parent
    return base_dir


def _junit_results(xml_path: Path) -> list[Result]:
    if not xml_path.is_file():
        return []
    results = []
    for case in ET.parse(xml_path).getroot().iter("testcase"):  # noqa: S314 (our own file)
        name = f"{case.get('classname', '')}::{case.get('name', '')}"
        tags = {child.tag for child in case}
        outcome = next(
            (
                o
                for tag, o in (("error", "error"), ("failure", "failed"), ("skipped", "skipped"))
                if tag in tags
            ),
            "passed",
        )
        results.append(Result(name, outcome))
    return results


def run_pytest(base_dir: Path, tests: list[NewTest], python: str) -> list[Result]:
    groups: dict[Path, list[str]] = defaultdict(list)
    for test in tests:
        run_dir = _run_dir(base_dir, test.path)
        rel = (base_dir / test.path).relative_to(run_dir).as_posix()
        groups[run_dir].append(f"{rel}::{test.name.replace('.', '::')}")
    results: list[Result] = []
    for run_dir, nodeids in groups.items():
        with tempfile.TemporaryDirectory() as tmp:
            xml_path = Path(tmp) / "red.xml"
            argv = [python, "-m", "pytest", "-p", "no:randomly", "-p", "no:cacheprovider"]
            argv += ["-o", "xfail_strict=false", "--runxfail", "--junitxml", str(xml_path)]
            # The base checkout's code comes first on sys.path, ahead of an editable
            # install of the head checkout.
            env = {**os.environ, "PYTHONPATH": str(run_dir)}
            subprocess.run([*argv, *nodeids], cwd=run_dir, env=env, check=False)  # noqa: S603
            results += _junit_results(xml_path)
    return results


def run_vitest(repo: Path, base_dir: Path, tests: list[NewTest]) -> list[Result]:
    frontend = base_dir / "frontend"
    modules = repo / "frontend" / "node_modules"
    if modules.is_dir() and not (frontend / "node_modules").exists():
        (frontend / "node_modules").symlink_to(modules, target_is_directory=True)
    files = sorted({test.path.removeprefix("frontend/") for test in tests})
    titles = {test.name.rsplit(" > ", 1)[-1] for test in tests}
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "vitest.json"
        subprocess.run(  # noqa: S603
            ["npx", "vitest", "run", "--reporter=json", f"--outputFile={out}", *files],  # noqa: S607
            cwd=frontend,
            check=False,
        )
        if not out.is_file():
            return []
        report = json.loads(out.read_text())
    status = {"passed": "passed", "failed": "failed"}
    return [
        Result(case["fullName"], status.get(case["status"], "skipped"))
        for suite in report.get("testResults", [])
        for case in suite.get("assertionResults", [])
        if case.get("title") in titles
    ]


def issues_from(args: argparse.Namespace) -> set[str]:
    if args.issues:
        return {issue.strip().lstrip("#") for issue in args.issues.split(",") if issue.strip()}
    return set(CLOSING.findall(os.environ.get("PR_BODY", "")))


def report(results: list[Result]) -> None:
    lines = [
        "## red-proof: new tests on the base commit",
        "",
        "| Test | On base |",
        "| --- | --- |",
    ]
    lines += [f"| `{r.test}` | {r.outcome} |" for r in results]
    text = "\n".join(lines) + "\n"
    print(text)
    _summary(text)


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--pr", default="")
    parser.add_argument("--issues", default="", help="comma-separated; default: PR_BODY closes")
    parser.add_argument("--python", default=sys.executable, help="interpreter with pytest")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse(argv)
    repo = Path(args.repo).resolve()
    new_tests = added_tests(repo, args.base, args.head)
    if not new_tests:
        return fail("A `bug` PR must add at least one test.")
    issues = issues_from(args)
    if not issues:
        return fail("No issue found: pass --issues or write `Fixes #<n>` in the PR body.")
    if not any(is_issue_named(test, issues) for test in new_tests):
        wanted = ", ".join(f"test_issue_{n}_..." for n in sorted(issues))
        return fail(f"At least one new test must be named for an issue this PR fixes: {wanted}")

    py_tests = [t for t in new_tests if not is_ts_test(t.path)]
    ts_tests = [t for t in new_tests if is_ts_test(t.path) and t.path.startswith("frontend/src/")]
    with worktree(repo, args.base) as base_dir:
        paths = {t.path for t in new_tests} | changed_conftests(repo, args.base, args.head)
        for path in paths:
            copy_head_version(repo, args.head, path, base_dir)
        results = run_pytest(base_dir, py_tests, args.python) if py_tests else []
        results += run_vitest(repo, base_dir, ts_tests) if ts_tests else []
    report(results)

    passing_on_base = [r for r in results if r.outcome == "passed"]
    not_run = [r for r in results if r.outcome in ("error", "skipped")]
    if passing_on_base:
        n = len(passing_on_base)
        return fail(f"{n} new test(s) already pass on the base commit")
    if not_run or not results:
        return fail(
            "new test(s) did not run on base (collection error or skip); make them fail on behavior"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
