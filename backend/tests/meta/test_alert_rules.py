"""Alert rules are code with unit tests (P0-27, REL-5): `promtool test rules` passes on
deploy/prometheus/alerts.test.yml, and every required alert has a firing case there.

promtool comes from PATH when installed, otherwise from the pinned prom/prometheus image
through Docker (as in CI). Without either, the test skips locally and fails in CI.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
RULES_DIR = REPO / "deploy" / "prometheus"
ALERTS = RULES_DIR / "alerts.yml"
ALERT_TESTS = RULES_DIR / "alerts.test.yml"
PROMETHEUS_IMAGE = "prom/prometheus:v3.15.0"
REQUIRED_ALERTS = frozenset(
    {
        "TumnisQueueBacklog",
        "TumnisDeadLettersGrowing",
        "TumnisWalArchiveStale",
        "TumnisWalArchiveFailing",
        "TumnisBackupMissing",
        "TumnisCertificateExpiring",
        "TumnisAuditChainBroken",
        "TumnisAuditChainNotVerified",
    }
)


def _promtool_command() -> list[str]:
    if shutil.which("promtool"):
        return ["promtool", "test", "rules", ALERT_TESTS.name]
    if shutil.which("docker"):
        return [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{RULES_DIR}:/rules:ro",
            "-w",
            "/rules",
            "--entrypoint",
            "promtool",
            PROMETHEUS_IMAGE,
            "test",
            "rules",
            ALERT_TESTS.name,
        ]
    if os.environ.get("CI"):
        pytest.fail("neither promtool nor docker is available in CI")
    pytest.skip("neither promtool nor docker is available")


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), f"{path.name} is not a YAML mapping"
    return data


@pytest.mark.contract
@pytest.mark.req("REL-5")
@pytest.mark.wp("P0-27")
@pytest.mark.xfail(strict=True, reason="spec:P0-27")
def test_promtool_passes() -> None:
    """T-P0-27-08
    `promtool test rules alerts.test.yml` exits 0: the rules load and every rule unit test
    (firing and quiet cases) passes.
    """
    assert ALERT_TESTS.is_file(), f"{ALERT_TESTS} is missing"
    result = subprocess.run(  # noqa: S603  # fixed argv, no shell
        _promtool_command(),
        cwd=RULES_DIR,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.contract
@pytest.mark.req("REL-5")
@pytest.mark.wp("P0-27")
@pytest.mark.xfail(strict=True, reason="spec:P0-27")
def test_required_alerts_exist_and_are_tested() -> None:
    """T-P0-27-09
    alerts.yml defines the eight required alerts, and alerts.test.yml (which loads
    alerts.yml) has at least one `alert_rule_test` with a non-empty `exp_alerts` for each.
    """
    rules = _load(ALERTS)
    defined = {
        rule["alert"] for group in rules["groups"] for rule in group["rules"] if "alert" in rule
    }
    assert defined >= REQUIRED_ALERTS, sorted(REQUIRED_ALERTS - defined)

    tests = _load(ALERT_TESTS)
    assert ALERTS.name in tests["rule_files"]
    firing = {
        case["alertname"]
        for test in tests["tests"]
        for case in test.get("alert_rule_test", [])
        if case.get("exp_alerts")
    }
    assert firing >= REQUIRED_ALERTS, f"no firing case for {sorted(REQUIRED_ALERTS - firing)}"
