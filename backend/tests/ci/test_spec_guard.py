"""spec-guard: a PR may not weaken a locked test (P0-03, Quality rule 1)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from textwrap import dedent

import pytest

from tests.ci._gitrepo import Repo, commit, make_repo
from tests.ci._scripts import load, node_with_typescript

CALC_TEST = dedent(
    """\
    import pytest


    def test_add() -> None:
        x = 3
        assert x == 3


    def test_sub() -> None:
        with pytest.raises(ZeroDivisionError):
            _ = 1 / 0
    """
)

SPEC_TEST = dedent(
    """\
    import pytest


    @pytest.mark.req("FR-3.1")
    @pytest.mark.wp("P0-18")
    @pytest.mark.xfail(strict=True, reason="spec:P0-18")
    def test_agent_cannot_move_backlog_to_done() -> None:
        \"\"\"T-P0-18-04\"\"\"
        assert move("backlog", "done") == "refused"
    """
)

SPEC_MODULE = dedent(
    """\
    import pytest

    pytestmark = [pytest.mark.integration, pytest.mark.xfail(strict=True, reason="spec:P0-18")]


    def test_rls_hides_other_workspace() -> None:
        assert rows("B") == []
    """
)


def _guard(repo: Repo, head: str, labels: str = "", pr: str = "7") -> tuple[int, list[str]]:
    """Run spec-guard's CLI and its collector; returns (exit code, sorted violation kinds)."""
    spec_guard = load("spec_guard")
    argv = ["--base", repo.base, "--head", head, "--repo", str(repo.path)]
    code = spec_guard.main([*argv, "--labels", labels, "--pr", pr])
    kinds = sorted(v.kind for v in spec_guard.collect(repo.path, repo.base, head))
    return code, kinds


@pytest.fixture(autouse=True)
def _no_ci_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep reports out of a real CI job summary and away from the real GitHub API."""
    for name in ("GITHUB_STEP_SUMMARY", "GH_API_STUB", "SPEC_CHANGE_ACTORS"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.req("Quality rule 1")
@pytest.mark.wp("P0-03")
def test_flags_edited_assertion(tmp_path: Path) -> None:
    """T-P0-03-01
    Changing `assert x == 3` to `assert x >= 3` in an existing test is edited_test.
    """
    repo = make_repo(tmp_path, {"backend/tests/test_calc.py": CALC_TEST})
    head = commit(repo, {"backend/tests/test_calc.py": CALC_TEST.replace("x == 3", "x >= 3")})

    assert _guard(repo, head) == (1, ["edited_test"])


@pytest.mark.req("Quality rule 1")
@pytest.mark.wp("P0-03")
def test_flags_deleted_test_and_deleted_file(tmp_path: Path) -> None:
    """T-P0-03-02
    Removing a test function, and deleting a test file, are violations.
    """
    without_sub = CALC_TEST[: CALC_TEST.index("\n\ndef test_sub")] + "\n"
    repo = make_repo(
        tmp_path,
        {"backend/tests/test_calc.py": CALC_TEST, "backend/tests/test_other.py": CALC_TEST},
    )
    head = commit(
        repo,
        {"backend/tests/test_calc.py": without_sub},
        delete=["backend/tests/test_other.py"],
    )

    assert _guard(repo, head) == (1, ["deleted_file", "deleted_test"])


@pytest.mark.req("Quality rule 1")
@pytest.mark.wp("P0-03")
def test_allows_removing_spec_xfail_marker_only(tmp_path: Path) -> None:
    """T-P0-03-03
    A diff that only drops the spec xfail passes, as a decorator or as a pytestmark entry.
    """
    marker = '@pytest.mark.xfail(strict=True, reason="spec:P0-18")\n'
    entry = ', pytest.mark.xfail(strict=True, reason="spec:P0-18")'
    repo = make_repo(
        tmp_path,
        {"backend/tests/test_state.py": SPEC_TEST, "backend/tests/test_rls.py": SPEC_MODULE},
    )
    head = commit(
        repo,
        {
            "backend/tests/test_state.py": SPEC_TEST.replace(marker, ""),
            "backend/tests/test_rls.py": SPEC_MODULE.replace(entry, ""),
        },
    )

    assert _guard(repo, head) == (0, [])


@pytest.mark.req("Quality rule 1")
@pytest.mark.wp("P0-03")
def test_flags_marker_removal_combined_with_edit(tmp_path: Path) -> None:
    """T-P0-03-04
    Dropping the marker and editing the body in the same test is still edited_test.
    """
    marker = '@pytest.mark.xfail(strict=True, reason="spec:P0-18")\n'
    edited = SPEC_TEST.replace(marker, "").replace('== "refused"', '!= "done"')
    repo = make_repo(tmp_path, {"backend/tests/test_state.py": SPEC_TEST})
    head = commit(repo, {"backend/tests/test_state.py": edited})

    assert _guard(repo, head) == (1, ["edited_test"])


@pytest.mark.req("Quality rule 1")
@pytest.mark.wp("P0-03")
def test_flags_added_skip_or_plain_xfail(tmp_path: Path) -> None:
    """T-P0-03-05
    Adding @pytest.mark.skip or a non-spec xfail to an existing test is a violation.
    """
    weakened = CALC_TEST.replace(
        "def test_add", '@pytest.mark.skip(reason="later")\ndef test_add'
    ).replace("def test_sub", '@pytest.mark.xfail(reason="flaky")\ndef test_sub')
    repo = make_repo(tmp_path, {"backend/tests/test_calc.py": CALC_TEST})
    head = commit(repo, {"backend/tests/test_calc.py": weakened})

    assert _guard(repo, head) == (1, ["added_skip_or_xfail", "added_skip_or_xfail"])


@pytest.mark.req("Quality rule 1")
@pytest.mark.wp("P0-03")
def test_skips_generated_contract_tests(tmp_path: Path) -> None:
    """T-P0-03-06
    Edits under backend/tests/contract/generated/ and in files with `# @generated` pass.
    """
    generated = "# @generated by make gen; do not edit\n" + CALC_TEST
    repo = make_repo(
        tmp_path,
        {
            "backend/tests/contract/generated/test_openapi.py": CALC_TEST,
            "backend/tumnis/modules/tasks/tests/contract/test_schema.py": generated,
        },
    )
    head = commit(
        repo,
        {
            "backend/tests/contract/generated/test_openapi.py": CALC_TEST.replace("== 3", ">= 3"),
            "backend/tumnis/modules/tasks/tests/contract/test_schema.py": generated.replace(
                "== 3", ">= 3"
            ),
        },
    )

    assert _guard(repo, head) == (0, [])


@pytest.mark.req("Quality rule 1")
@pytest.mark.wp("P0-03")
def test_allows_new_tests_and_pure_moves(tmp_path: Path) -> None:
    """T-P0-03-07
    Adding tests, and renaming a file with unchanged tests, pass.
    """
    extra = "\n\ndef test_mul() -> None:\n    assert 2 * 3 == 6\n"
    repo = make_repo(
        tmp_path,
        {"backend/tests/test_calc.py": CALC_TEST, "backend/tests/test_move_me.py": SPEC_TEST},
    )
    head = commit(
        repo,
        {
            "backend/tests/test_calc.py": CALC_TEST + extra,
            "backend/tests/test_new.py": CALC_TEST,
            "backend/tumnis/modules/tasks/tests/unit/test_moved.py": SPEC_TEST,
        },
        delete=["backend/tests/test_move_me.py"],
    )

    assert _guard(repo, head) == (0, [])


@pytest.mark.req("Quality rule 1")
@pytest.mark.wp("P0-03")
def test_ci_files_and_other_non_tests_are_not_locked(tmp_path: Path) -> None:
    """T-P0-03-18
    spec-guard locks only existing test files (Scott's call, 2026-09-29): editing or
    deleting a workflow under .github/, a guard script under scripts/ci/ or any other file
    that is not a test passes.
    """
    workflow = "name: ci\non: [pull_request]\n"
    repo = make_repo(
        tmp_path,
        {
            ".github/workflows/ci.yml": workflow,
            ".github/CODEOWNERS": "* @scott\n",
            "scripts/ci/spec_guard.py": "LOCKED = True\n",
            "deploy/compose.test.yaml": "services: {}\n",
        },
    )
    head = commit(
        repo,
        {
            ".github/workflows/ci.yml": workflow + "jobs: {}\n",
            "scripts/ci/spec_guard.py": "LOCKED = False\n",
            "deploy/compose.test.yaml": "services: {api: {}}\n",
        },
        delete=[".github/CODEOWNERS"],
    )

    assert _guard(repo, head) == (0, [])


VITEST_SPEC = dedent(
    """\
    import { expect, test } from "vitest";

    test.fails("[P0-25][FR-3.10] replays each queued item once", () => {
      expect(1 + 1).toBe(2);
    });
    """
)

PLAYWRIGHT_SPEC = dedent(
    """\
    import { expect, test } from "@playwright/test";

    test.describe("quick-add", () => {
      test("from any route", { tag: ["@J2", "@FR-3.3", "@P0-05"] }, async ({ page }) => {
        test.fail();
        await page.goto("/");
        await expect(page.getByRole("textbox")).toBeVisible();
      });
    });
    """
)


@pytest.mark.req("Quality rule 1")
@pytest.mark.wp("P0-03")
def test_typescript_fails_to_test_flip_is_allowed_edit_is_not(tmp_path: Path) -> None:
    """T-P0-03-08
    `test.fails(` to `test(` passes; changing an `expect` in the same test fails; removing
    `test.fail();` in a Playwright body passes.
    """
    if not node_with_typescript():
        assert not os.environ.get("CI"), "CI must provide node and frontend/node_modules"
        pytest.skip("node or frontend/node_modules/typescript missing; run npm ci in frontend/")
    vitest, e2e = "frontend/src/lib/queue.test.ts", "frontend/e2e/quick-add.spec.ts"
    flipped = VITEST_SPEC.replace("test.fails(", "test(")
    repo = make_repo(tmp_path, {vitest: VITEST_SPEC, e2e: PLAYWRIGHT_SPEC})

    green = commit(repo, {vitest: flipped, e2e: PLAYWRIGHT_SPEC.replace("    test.fail();\n", "")})
    assert _guard(repo, green) == (0, [])

    weakened = commit(repo, {vitest: flipped.replace("toBe(2)", "toBeGreaterThan(1)")})
    assert _guard(repo, weakened) == (1, ["edited_test"])


def _events_stub(tmp_path: Path, login: str) -> Path:
    stub = tmp_path / f"events-{login}.json"
    events = [
        {"event": "labeled", "label": {"name": "wp:P0-18"}, "actor": {"login": "scott"}},
        {"event": "labeled", "label": {"name": "spec-change"}, "actor": {"login": login}},
    ]
    stub.write_text(json.dumps(events))
    return stub


@pytest.mark.req("Quality rule 1")
@pytest.mark.wp("P0-03")
def test_spec_change_label_only_counts_from_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """T-P0-03-09
    The spec-change label added by the owner waives with a report; the same label added by
    another login does not.
    """
    repo = make_repo(tmp_path, {"backend/tests/test_calc.py": CALC_TEST})
    head = commit(repo, {"backend/tests/test_calc.py": CALC_TEST.replace("x == 3", "x >= 3")})
    monkeypatch.setenv("SPEC_CHANGE_ACTORS", "scott")

    monkeypatch.setenv("GH_API_STUB", str(_events_stub(tmp_path, "scott")))
    code, kinds = _guard(repo, head, labels="wp:P0-18,spec-change")
    report = capsys.readouterr().out
    assert (code, kinds) == (0, ["edited_test"])
    assert "waived" in report.lower()
    assert "test_add" in report

    monkeypatch.setenv("GH_API_STUB", str(_events_stub(tmp_path, "bot-user")))
    assert _guard(repo, head, labels="wp:P0-18,spec-change") == (1, ["edited_test"])


SKILL_CASE = dedent(
    """\
    id: enrich-ai-only-report
    profile: project-template
    skill: enrich
    input: ../../recordings/enrich/ai_only_report.packet.json
    output_schema: {family: enrichment, name: result, version: 1}
    meta:
      test_id: T-P1-05-02
      req: [FR-4.4]
      wp: P1-05
      xfail: spec:P1-05
    expect:
      json:
        - {path: "$.estimate_minutes", absent: true}
    """
)


@pytest.mark.req("Quality rule 1")
@pytest.mark.wp("P1-05")
def test_skill_case_may_drop_only_its_spec_marker(tmp_path: Path) -> None:
    """A skill case whose only change is dropping `xfail: spec:<WP>` passes, as a spec
    xfail's removal does in Python; dropping it along with an edit is still edited_test."""
    path = "profiles/tests/cases/enrich/ai_only_report.yaml"
    unmarked = SKILL_CASE.replace("  xfail: spec:P1-05\n", "")
    repo = make_repo(tmp_path, {path: SKILL_CASE})
    assert _guard(repo, commit(repo, {path: unmarked})) == (0, [])

    (tmp_path / "edited").mkdir()
    repo = make_repo(tmp_path / "edited", {path: SKILL_CASE})
    weakened = unmarked.replace("absent: true", "type: integer")
    assert _guard(repo, commit(repo, {path: weakened})) == (1, ["edited_test"])
