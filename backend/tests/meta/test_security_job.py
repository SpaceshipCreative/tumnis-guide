"""The Security CI job blocks merges on high-severity findings, and the custom Semgrep rules
prove themselves (P0-16, SEC-7).

`.semgrep/tumnis.yml` holds the repository's own rules; `.semgrep/tumnis.*` beside it are
their test files (`ruleid:` lines must match, `ok:` lines must not), run with
`semgrep --test`. Semgrep is not a project dependency (it pins an older OpenTelemetry), so
the test runs the version ci.yml pins through `uvx`.
"""

from __future__ import annotations

import datetime as dt
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
CI_YML = REPO / ".github" / "workflows" / "ci.yml"
TRIVYIGNORE = REPO / ".trivyignore"
SEMGREP_DIR = REPO / ".semgrep"
SEMGREP_PIN = re.compile(r"semgrep==(\d+\.\d+\.\d+)")
CUSTOM_RULES = (
    "tumnis-secret-eq",
    "tumnis-raw-httpx",
    "tumnis-no-requests",
    "tumnis-sql-fstring",
    "tumnis-inner-html",
    "tumnis-log-body",
)
MAX_IGNORE_DAYS = 30  # plan default: an ignored finding expires within 30 days
ISSUE_LINK = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/issues/\d+")
IGNORE_ENTRY = re.compile(r"^(?P<id>[A-Za-z0-9._:-]+)(?P<rest>.*)$")
EXPIRY = re.compile(r"\bexp:(\d{4}-\d{2}-\d{2})\b")


def security_steps() -> list[str]:
    """The `run:` scripts and `uses:` references of the Security job, in order."""
    jobs: dict[str, dict[str, Any]] = yaml.safe_load(CI_YML.read_text())["jobs"]
    steps = jobs["security"]["steps"]
    return [str(step.get("run") or step.get("uses") or "") for step in steps]


def trivyignore_problems(text: str, today: dt.date) -> list[str]:
    """Each entry needs an issue link (on its line or in the comment right above it) and an
    `exp:YYYY-MM-DD` expiry no more than MAX_IGNORE_DAYS after `today`."""
    problems: list[str] = []
    previous = ""
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            previous = ""
            continue
        if line.startswith("#"):
            previous = line
            continue
        entry = IGNORE_ENTRY.match(line)
        assert entry is not None  # the pattern matches any non-empty line
        if not ISSUE_LINK.search(line) and not ISSUE_LINK.search(previous):
            problems.append(f"line {number}: {entry['id']} has no issue link")
        expiry = EXPIRY.search(entry["rest"])
        if expiry is None:
            problems.append(f"line {number}: {entry['id']} has no exp:YYYY-MM-DD")
        elif dt.date.fromisoformat(expiry.group(1)) > today + dt.timedelta(days=MAX_IGNORE_DAYS):
            problems.append(f"line {number}: {entry['id']} expires more than 30 days out")
        previous = ""
    return problems


@pytest.mark.req("SEC-7")
@pytest.mark.wp("P0-16")
@pytest.mark.slow
def test_semgrep_rules_pass_their_own_tests() -> None:
    """T-P0-16-16
    `semgrep --test .semgrep` (the version ci.yml pins) exits 0: each custom rule fires on
    its `ruleid:` lines and on none of its `ok:` lines, and every rule has test cases.
    """
    pins = set(SEMGREP_PIN.findall("\n".join(security_steps())))
    assert len(pins) == 1, pins
    rules = yaml.safe_load((SEMGREP_DIR / "tumnis.yml").read_text())["rules"]
    assert {rule["id"] for rule in rules} == set(CUSTOM_RULES)
    tests = "\n".join(
        path.read_text() for path in SEMGREP_DIR.glob("tumnis.*") if path.suffix != ".yml"
    )
    for rule in CUSTOM_RULES:
        assert f"ruleid: {rule}" in tests, rule
        assert f"ok: {rule}" in tests, rule

    uvx = shutil.which("uvx")
    assert uvx is not None, "uv is required (it runs the pinned semgrep)"
    result = subprocess.run(  # noqa: S603  # our pinned semgrep on our own rule files
        [uvx, "--from", f"semgrep=={pins.pop()}", "semgrep", "--test", "--metrics=off", "."],
        cwd=SEMGREP_DIR,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0/0" not in result.stdout, result.stdout


@pytest.mark.req("SEC-7")
@pytest.mark.wp("P0-16")
def test_security_job_blocks_on_high_findings() -> None:
    """T-P0-16-17
    The Security job runs pip-audit (`--strict`), npm audit (`--audit-level=high`), Trivy on
    the built image (`--severity HIGH,CRITICAL --exit-code 1`), Semgrep with the custom rules
    (`--error`) and gitleaks, none of them allowed to fail quietly; `.trivyignore` entries
    each carry an issue link and an expiry at most 30 days out.
    """
    jobs: dict[str, dict[str, Any]] = yaml.safe_load(CI_YML.read_text())["jobs"]
    job = jobs["security"]
    assert "continue-on-error" not in job
    assert all("continue-on-error" not in step for step in job["steps"])
    steps = security_steps()
    script = "\n".join(steps)
    assert "|| true" not in script

    def step_with(tool: str) -> str:
        found = [step for step in steps if tool in step]
        assert found, f"no {tool} step"
        return found[0]

    assert "--strict" in step_with("pip-audit")
    assert "--audit-level=high" in step_with("npm audit")
    trivy = step_with("trivy")
    assert "--exit-code 1" in trivy
    assert "--severity HIGH,CRITICAL" in trivy
    semgrep = step_with("semgrep scan")
    assert "--error" in semgrep
    assert ".semgrep/tumnis.yml" in semgrep
    gitleaks = step_with("gitleaks")
    assert "--exit-code 1" in gitleaks or gitleaks.startswith("gitleaks/gitleaks-action@")
    for uses in (step for step in steps if "@" in step and "\n" not in step and " " not in step):
        assert re.search(r"@[0-9a-f]{40}$", uses), f"{uses} is not pinned by commit SHA"

    assert trivyignore_problems(TRIVYIGNORE.read_text(), dt.datetime.now(dt.UTC).date()) == []
    today = dt.date(2026, 3, 9)
    good = "# https://github.com/SpaceshipCreative/tumnis-guide/issues/7 no fix yet\n"
    good += "CVE-2026-0001 exp:2026-04-01\n"
    assert trivyignore_problems(good, today) == []
    assert trivyignore_problems("CVE-2026-0001 exp:2026-04-01\n", today) == [
        "line 1: CVE-2026-0001 has no issue link"
    ]
    linked = "CVE-2026-0002 https://github.com/o/r/issues/1"
    assert trivyignore_problems(f"{linked}\n", today) == [
        "line 1: CVE-2026-0002 has no exp:YYYY-MM-DD"
    ]
    assert trivyignore_problems(f"{linked} exp:2026-05-01\n", today) == [
        "line 1: CVE-2026-0002 expires more than 30 days out"
    ]
