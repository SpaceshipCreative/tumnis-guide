"""npm_audit_ignore: a high npm advisory with no fix yet can be ignored until an expiry date
(P0-16, SEC-7). `npm audit` has no ignore list of its own."""

from __future__ import annotations

import datetime as dt
import json
from typing import TYPE_CHECKING, Any

import pytest

from tests.ci._scripts import load

if TYPE_CHECKING:
    from pathlib import Path

PICOMATCH = "GHSA-fc5q-39xc-vjpf"
OTHER = "GHSA-9jrq-w75r-8gcw"
ISSUE = "# https://github.com/SpaceshipCreative/tumnis-guide/issues/204 no fixed version yet\n"
IGNORED = f"{ISSUE}{PICOMATCH} exp:2026-12-17\n"
BEFORE, AFTER = dt.date(2026, 11, 20), dt.date(2026, 12, 18)


def _advisory(ghsa: str, severity: str) -> dict[str, Any]:
    return {"url": f"https://github.com/advisories/{ghsa}", "severity": severity, "title": ghsa}


def _report(*advisories: dict[str, Any]) -> str:
    """An `npm audit --json` report (version 2): each advisory on its own package, and a
    package that is only affected through them (its `via` names packages, not advisories)."""
    packages: dict[str, Any] = {
        f"pkg{n}": {"name": f"pkg{n}", "severity": advisory["severity"], "via": [advisory]}
        for n, advisory in enumerate(advisories)
    }
    packages["vite"] = {"name": "vite", "severity": "high", "via": list(packages)}
    return json.dumps({"auditReportVersion": 2, "vulnerabilities": packages, "metadata": {}})


def _audit(tmp_path: Path, report: str, ignores: str, today: dt.date) -> int:
    """Run the script on a saved report and an ignore file, as of `today`."""
    script = load("npm_audit_ignore")
    saved, ignore_file = tmp_path / "audit.json", tmp_path / "npmauditignore"
    saved.write_text(report)
    ignore_file.write_text(ignores)
    code: int = script.main([str(saved), "--ignore-file", str(ignore_file)], today=today)
    return code


@pytest.mark.req("SEC-7")
@pytest.mark.wp("P0-16")
def test_an_ignored_high_advisory_stops_blocking_until_its_expiry(tmp_path: Path) -> None:
    """FIX-npm-audit-ignore-01
    Given a report whose only high advisory is in the ignore file, the script exits 0 until
    the entry's expiry date and 1 after it. It exits 1 when the advisory has no entry, and
    when another high or critical advisory has none. Moderate advisories never block.
    """
    report = _report(_advisory(PICOMATCH, "high"), _advisory("GHSA-f86p-h4j2-7qgj", "moderate"))

    assert _audit(tmp_path, report, IGNORED, BEFORE) == 0
    assert _audit(tmp_path, report, IGNORED, AFTER) == 1
    assert _audit(tmp_path, report, "", BEFORE) == 1
    assert _audit(tmp_path, report, f"{PICOMATCH}\n", BEFORE) == 1  # no expiry, not honoured
    for severity in ("high", "critical"):
        two = _report(_advisory(PICOMATCH, "high"), _advisory(OTHER, severity))
        assert _audit(tmp_path, two, IGNORED, BEFORE) == 1
    assert _audit(tmp_path, _report(_advisory(OTHER, "moderate")), "", BEFORE) == 0


@pytest.mark.req("SEC-7")
@pytest.mark.wp("P0-16")
def test_a_report_npm_could_not_produce_blocks(tmp_path: Path) -> None:
    """FIX-npm-audit-ignore-02
    Given output that is not an audit report (npm's error object when the registry cannot
    be reached, or nothing at all), the script exits non-zero whatever the ignore file says.
    """
    failed = json.dumps({"error": {"code": "ENOTFOUND", "summary": "request failed"}})

    assert _audit(tmp_path, failed, IGNORED, BEFORE) != 0
    assert _audit(tmp_path, "", IGNORED, BEFORE) != 0
