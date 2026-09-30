"""Shared by spec-guard and red-proof: git plumbing and locked test blocks.

A "locked block" is anything spec-guard protects inside a test file: test functions and
methods, state-machine rules and invariants, any helper whose body asserts, and the
module-level `pytestmark`. Blocks are compared as ASTs, so formatting never counts.
"""

from __future__ import annotations

import ast
import copy
import fnmatch
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

PY_TEST_NAMES = ("test_*.py", "*_test.py")
TS_TEST_GLOBS = (
    "frontend/src/**/*.test.ts",
    "frontend/src/**/*.test.tsx",
    "frontend/e2e/**/*.spec.ts",
)
CASE_GLOBS = ("profiles/tests/cases/*.yaml",)
TEST_GLOBS = ("**/test_*.py", "**/*_test.py", *TS_TEST_GLOBS, *CASE_GLOBS)


def _glob(path: str, pattern: str) -> bool:
    """fnmatch where `**/` may also match nothing (so `a/**/b` matches `a/b`)."""
    return fnmatch.fnmatch(path, pattern) or (
        "**/" in pattern and fnmatch.fnmatch(path, pattern.replace("**/", ""))
    )


def matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(_glob(path, pattern) for pattern in patterns)


def is_python_test(path: str) -> bool:
    name = PurePosixPath(path).name
    return any(fnmatch.fnmatch(name, pattern) for pattern in PY_TEST_NAMES)


def is_ts_test(path: str) -> bool:
    return matches_any(path, TS_TEST_GLOBS)


def is_case_file(path: str) -> bool:
    return matches_any(path, CASE_GLOBS)


# Golden files a contract test compares against (P2-02's task packets): data, locked like a
# test, so an agent cannot rewrite a golden to make its test pass.
GOLDEN_GLOBS = ("**/tests/contract/golden/**",)


def is_golden_file(path: str) -> bool:
    return matches_any(path, GOLDEN_GLOBS)


def is_test_file(path: str) -> bool:
    return is_python_test(path) or is_ts_test(path) or is_case_file(path) or is_golden_file(path)


# --- git ------------------------------------------------------------------------------


def _git_env() -> dict[str, str]:
    # A git hook exports GIT_DIR and GIT_INDEX_FILE; they would point every call at the
    # hook's repository instead of --repo.
    drop = {"GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_PREFIX"}
    return {key: value for key, value in os.environ.items() if key not in drop}


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 (fixed argv, no shell)
        ["git", "-c", "core.quotepath=off", *args],  # noqa: S607
        cwd=repo,
        env=_git_env(),
        capture_output=True,
        text=True,
        check=check,
    )


def show(repo: Path, sha: str, path: str) -> str | None:
    """File content at `sha`, or None when the path does not exist there."""
    result = git(repo, "show", f"{sha}:{path}", check=False)
    return result.stdout if result.returncode == 0 else None


@dataclass(frozen=True)
class Change:
    status: str  # A, M, D or R (copies are reported as A)
    old: str | None
    new: str | None


def changes(repo: Path, base: str, head: str) -> list[Change]:
    """`git diff --name-status -M base...head` as Change records."""
    raw = git(repo, "diff", "--name-status", "-z", "-M", f"{base}...{head}").stdout
    fields = raw.split("\0")
    result: list[Change] = []
    index = 0
    while index < len(fields) and fields[index]:
        status = fields[index][0]
        if status in "RC":
            old, new = fields[index + 1], fields[index + 2]
            index += 3
            result.append(Change("R", old, new) if status == "R" else Change("A", None, new))
            continue
        path = fields[index + 1]
        index += 2
        if status == "D":
            result.append(Change("D", path, None))
        elif status == "A":
            result.append(Change("A", None, path))
        else:  # M, T (type change) and anything else git reports on an existing path
            result.append(Change("M", path, path))
    return result


# --- TypeScript blocks (Vitest and Playwright) ----------------------------------------

TS_TESTS = Path(__file__).resolve().parent / "ts_tests.mjs"
TsBlock = dict[str, object]  # title, line, tags, fails, skip, text (see ts_tests.mjs)


def ts_blocks(sources: Mapping[str, str]) -> dict[str, dict[str, TsBlock]]:
    """{label: TypeScript source} to {label: {"<describe> > <title>": block}} via Node."""
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node is required to compare TypeScript tests (ts_tests.mjs)")
    with tempfile.TemporaryDirectory() as tmp:
        paths: dict[str, str] = {}
        for index, (label, source) in enumerate(sources.items()):
            path = Path(tmp) / f"{index}{''.join(PurePosixPath(label).suffixes)}"
            path.write_text(source)
            paths[str(path)] = label
        result = subprocess.run(  # noqa: S603 (fixed argv, no shell)
            [node, str(TS_TESTS), *paths],
            capture_output=True,
            text=True,
            check=True,
        )
    raw: dict[str, dict[str, TsBlock]] = json.loads(result.stdout)
    return {paths[path]: blocks for path, blocks in raw.items()}


# --- Python blocks --------------------------------------------------------------------

SPEC_XFAIL_NAMES = ("pytest.mark.xfail", "mark.xfail", "xfail")
WEAKENING = ("skip", "skipif", "xfail")


def is_spec_xfail(dec: ast.expr) -> bool:
    """True for pytest.mark.xfail(..., reason="spec:...") in any import spelling."""
    if not isinstance(dec, ast.Call) or ast.unparse(dec.func) not in SPEC_XFAIL_NAMES:
        return False
    return any(
        kw.arg == "reason"
        and isinstance(kw.value, ast.Constant)
        and str(kw.value.value).startswith("spec:")
        for kw in dec.keywords
    )


def is_weakening_decorator(dec: ast.expr) -> bool:
    name = ast.unparse(dec.func if isinstance(dec, ast.Call) else dec)
    return name.rsplit(".", 1)[-1] in WEAKENING and not is_spec_xfail(dec)


def _asserts(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Assert)
        or (isinstance(n, ast.Call) and ast.unparse(n.func).endswith("raises"))
        for n in ast.walk(node)
    )


def _is_rule(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        ast.unparse(dec).split("(")[0].endswith(("rule", "invariant"))
        for dec in node.decorator_list
    )


def locked_blocks(src: str) -> dict[str, ast.AST]:
    """Test functions and methods, state-machine rules, and any helper whose body asserts."""
    tree = ast.parse(src)
    out: dict[str, ast.AST] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                before = len(out)
                visit(child, f"{prefix}{child.name}.")
                if len(out) > before:
                    # The class's own markers (a skip there skips every method).
                    shell = copy.copy(child)
                    shell.body = [ast.Pass()]
                    out[f"{prefix}{child.name}"] = shell
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) and (
                child.name.startswith("test") or _asserts(child) or _is_rule(child)
            ):
                out[f"{prefix}{child.name}"] = child

    visit(tree, "")
    pytestmark = next(
        (
            n
            for n in tree.body
            if isinstance(n, ast.Assign) and any(ast.unparse(t) == "pytestmark" for t in n.targets)
        ),
        None,
    )
    if pytestmark is not None:
        out["<pytestmark>"] = pytestmark
    return out


def dump(node: ast.AST) -> str:
    return ast.dump(node, annotate_fields=True, include_attributes=False)


@dataclass(frozen=True)
class NewTest:
    path: str  # repo-relative file
    name: str  # "test_x" or "TestClass.test_x", or a Vitest/Playwright key


def added_python_tests(repo: Path, base: str, head: str) -> list[NewTest]:
    """Test functions present on head and not on base (new files or new names)."""
    found: list[NewTest] = []
    for change in changes(repo, base, head):
        if change.new is None or not is_python_test(change.new):
            continue
        head_src = show(repo, head, change.new) or ""
        base_src = show(repo, base, change.old) if change.old else None
        head_names = _test_names(head_src)
        base_names = _test_names(base_src) if base_src is not None else set()
        found.extend(NewTest(change.new, name) for name in sorted(head_names - base_names))
    return found


def _test_names(src: str) -> set[str]:
    try:
        blocks = locked_blocks(src)
    except SyntaxError:
        return set()
    return {name for name in blocks if name.rsplit(".", 1)[-1].startswith("test")}
