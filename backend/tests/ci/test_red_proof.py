"""red-proof: a bug-fix test must fail on the base commit (P0-03, Quality rule 2)."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from tests.ci._gitrepo import Repo, commit, make_repo
from tests.ci._scripts import load

BUGGY = "def add(a: int, b: int) -> int:\n    return a - b\n"
FIXED = "def add(a: int, b: int) -> int:\n    return a + b\n"
EXISTING_TEST = dedent(
    """\
    from calc import add


    def test_add_zero() -> None:
        assert add(0, 0) == 0
    """
)


def _bug_repo(tmp_path: Path) -> Repo:
    return make_repo(tmp_path, {"calc.py": BUGGY, "tests/test_calc.py": EXISTING_TEST})


def _with_test(name: str, body: str) -> str:
    return EXISTING_TEST + f"\n\ndef {name}() -> None:\n    {body}\n"


def _red_proof(repo: Repo, head: str, issues: str = "42") -> int:
    red_proof = load("red_proof")
    argv = ["--base", repo.base, "--head", head, "--repo", str(repo.path)]
    code: int = red_proof.main([*argv, "--pr", "5", "--issues", issues])
    return code


@pytest.fixture(autouse=True)
def _no_ci_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


@pytest.mark.req("Quality rule 2")
@pytest.mark.wp("P0-03")
@pytest.mark.xfail(strict=True, reason="spec:P0-03")
def test_fails_when_bug_test_passes_on_base(tmp_path: Path) -> None:
    """T-P0-03-10
    A new test_issue_42_* that already passes on the base commit makes red-proof exit 1.
    """
    repo = _bug_repo(tmp_path)
    head = commit(
        repo,
        {
            "calc.py": FIXED,
            "tests/test_calc.py": _with_test("test_issue_42_add_is_sum", "assert add(0, 0) == 0"),
        },
    )

    assert _red_proof(repo, head) == 1


@pytest.mark.req("Quality rule 2")
@pytest.mark.wp("P0-03")
@pytest.mark.xfail(strict=True, reason="spec:P0-03")
def test_passes_when_bug_test_fails_on_base(tmp_path: Path) -> None:
    """T-P0-03-11
    The same test failing on the base commit (the bug is there) exits 0.
    """
    repo = _bug_repo(tmp_path)
    head = commit(
        repo,
        {
            "calc.py": FIXED,
            "tests/test_calc.py": _with_test("test_issue_42_add_is_sum", "assert add(2, 3) == 5"),
        },
    )

    assert _red_proof(repo, head) == 0


@pytest.mark.req("Quality rule 2")
@pytest.mark.wp("P0-03")
@pytest.mark.xfail(strict=True, reason="spec:P0-03")
def test_requires_issue_named_test(tmp_path: Path) -> None:
    """T-P0-03-12
    A bug PR with no new test, or with no test_issue_<n>_ name for its issue, exits 1.
    """
    repo = _bug_repo(tmp_path)
    no_test = commit(repo, {"calc.py": FIXED})
    assert _red_proof(repo, no_test) == 1

    unnamed = commit(
        repo, {"tests/test_calc.py": _with_test("test_add_is_sum", "assert add(2, 3) == 5")}
    )
    assert _red_proof(repo, unnamed) == 1

    other_issue = commit(
        repo,
        {"tests/test_calc.py": _with_test("test_issue_7_add_is_sum", "assert add(2, 3) == 5")},
    )
    assert _red_proof(repo, other_issue, issues="42") == 1
