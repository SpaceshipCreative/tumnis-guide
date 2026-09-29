"""Toolchain: Ruff, format and mypy clean; Renovate policy (P0-01)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent


def _run(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 (fixed argv, no shell)
        [sys.executable, "-m", *argv],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.req("SEC-7")
@pytest.mark.wp("P0-01")
def test_ruff_format_and_mypy_clean() -> None:
    """T-P0-01-10
    Ruff check, Ruff format check and mypy exit 0 on the tree.
    """
    for argv in (
        ("ruff", "check", "."),
        ("ruff", "format", "--check", "."),
        ("mypy", "tumnis"),
    ):
        result = _run(*argv)
        assert result.returncode == 0, f"{' '.join(argv)}\n{result.stdout}{result.stderr}"


def _rules_for(config: dict[str, Any], package: str) -> list[dict[str, Any]]:
    return [
        rule
        for rule in config.get("packageRules", [])
        if any(package in name for name in rule.get("matchPackageNames", []))
    ]


@pytest.mark.req("SEC-7")
@pytest.mark.wp("P0-01")
@pytest.mark.xfail(strict=True, reason="spec:P0-01")
def test_renovate_pins_and_groups() -> None:
    """T-P0-01-12
    renovate.json pins exact versions, groups Tiptap and DBOS as manual-merge
    with the needs-suite label, and keeps lockfile maintenance on.
    """
    config = json.loads((REPO / "renovate.json").read_text())

    assert "config:recommended" in config["extends"]
    assert ":pinAllExceptPeerDependencies" in config["extends"]
    assert config["lockFileMaintenance"]["enabled"] is True

    for package in ("@tiptap/", "dbos"):
        rules = _rules_for(config, package)
        assert rules, f"no package rule for {package}"
        for rule in rules:
            assert rule["automerge"] is False, package
            assert "needs-suite" in rule["labels"], package
            assert rule.get("groupName"), package

    patch_rules = [
        rule
        for rule in config["packageRules"]
        if rule.get("automerge") is True and rule.get("matchUpdateTypes") == ["patch"]
    ]
    assert patch_rules, "patch releases should automerge after the pipeline passes"
    for rule in patch_rules:
        assert rule.get("ignoreTests", False) is False

    managers = config.get("customManagers", [])
    assert any(
        manager.get("customType") == "regex"
        and any("profiles" in pattern for pattern in manager.get("managerFilePatterns", []))
        for manager in managers
    ), "profiles/*/VERSION pins need a regex manager"
