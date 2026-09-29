"""Protected import contracts: the Generation slot's callers (P1-03, FR-11.8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.meta._lint_tree import LINT_PACKAGE, make_lint_tree, run_lint_imports

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GENERATION_FILES = {
    f"{LINT_PACKAGE}/modules/decisions/generation_api.py": '"""generation_api"""\n',
    f"{LINT_PACKAGE}/modules/decisions/adapters/__init__.py": "",
    f"{LINT_PACKAGE}/modules/decisions/adapters/vllm_generation.py": '"""vllm_generation"""\n',
}


@pytest.mark.req("FR-11.8")
@pytest.mark.wp("P1-03")
def test_generation_protected_contract_rejects_other_importers(tmp_path: Path) -> None:
    """T-P1-03-04
    Given a temporary copy of `.importlinter` and the fixture
    `tests/meta/fixtures/bad_generation_import.py` placed as `modules/planning/_bad.py` in a
    temp tree, when `lint-imports --config` runs on it, then it fails and names
    `generation-callers`.
    """
    bad = (FIXTURES / "bad_generation_import.py").read_text().replace("tumnis", LINT_PACKAGE)
    config = make_lint_tree(
        tmp_path,
        {**GENERATION_FILES, f"{LINT_PACKAGE}/modules/planning/_bad.py": bad},
    )
    result = run_lint_imports(tmp_path, config)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "generation-callers" in result.stdout
    assert f"{LINT_PACKAGE}.modules.planning._bad -> " in result.stdout


@pytest.mark.req("FR-11.8")
@pytest.mark.wp("P1-03")
def test_generation_allowed_callers_pass(tmp_path: Path) -> None:
    """The enrichment workflow and focus delivery may import the Generation slot:
    `generation_api` counts as a second public interface of decisions, so the module
    boundary contract keeps too."""
    use = f"from {LINT_PACKAGE}.modules.decisions import generation_api\n"
    config = make_lint_tree(
        tmp_path,
        {
            **GENERATION_FILES,
            f"{LINT_PACKAGE}/modules/agents/workflows.py": use,
            f"{LINT_PACKAGE}/modules/notifications/focus_voice.py": use,
        },
    )
    result = run_lint_imports(tmp_path, config)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.req("FR-11.8")
@pytest.mark.wp("P1-03")
def test_generation_adapter_is_protected_too(tmp_path: Path) -> None:
    """Reaching the vLLM Generation adapter directly, bypassing `generation_api`, breaks the
    same contract (and a module router importing it breaks api-never-calls-out)."""
    use = f"from {LINT_PACKAGE}.modules.decisions.adapters import vllm_generation\n"
    config = make_lint_tree(
        tmp_path, {**GENERATION_FILES, f"{LINT_PACKAGE}/modules/focus/router.py": use}
    )
    result = run_lint_imports(tmp_path, config)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "Only enrichment and focus delivery may use the Generation slot BROKEN" in (
        result.stdout
    )
    assert "The api process never imports Jev, its SDK or VllmGeneration BROKEN" in result.stdout
