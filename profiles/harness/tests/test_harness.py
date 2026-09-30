"""The harness around the spec tests: configuration guards, how a reply is judged, and how
an expected-failure case is reported (P1-05)."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from harness.cases import Case, load_case
from harness.report import junit_xml, summary_line, verdict
from harness.run import (
    Attempt,
    CaseResult,
    HarnessError,
    HermesRunner,
    attempt_from_stream,
    load_config,
    run_case,
)
from harness.tests._cases import TASK_ID, write_case

REPLY = {
    "schema_version": 1,
    "task_id": TASK_ID,
    "first_action": "Open the Acme contract PDF and add the signature field",
    "acceptance_criteria": ["Acme has the signed contract"],
    "estimate_minutes": 20,
    "label_revision": None,
    "hybrid_split": None,
}


def _stream(text: str, **final: object) -> str:
    record = {"type": "result", "session_id": "s-1", "exit_code": 0, "text": text, **final}
    return json.dumps(record) + "\n"


@pytest.fixture
def case(tmp_path: Path) -> Case:
    return load_case(write_case(tmp_path))


def test_a_valid_reply_passes(case: Case) -> None:
    attempt = attempt_from_stream(case, _stream(json.dumps(REPLY)))
    assert attempt.outcome == "pass", attempt.failures
    assert attempt.tool_calls == ()


def test_prose_around_the_json_fails_as_production_does(case: Case) -> None:
    attempt = attempt_from_stream(case, _stream("Here you go: " + json.dumps(REPLY)))
    assert attempt.outcome == "fail"
    assert attempt.failures[0] == "run failed: no_json"


def test_a_missing_result_record_and_a_timeout_fail(case: Case) -> None:
    assert attempt_from_stream(case, "").failures[0] == "run failed: no_result_record"
    timed_out = attempt_from_stream(case, "", exit_code=None, timed_out=True)
    assert timed_out.failures[0] == "run timed_out: timed_out"


def test_schema_rules_and_checks_are_all_applied(case: Case) -> None:
    ai_estimate = {**REPLY, "label_revision": {"label": "ai", "reason": "A script can do it"}}
    failures = attempt_from_stream(case, _stream(json.dumps(ai_estimate))).failures
    assert "rule enrichment_errors: estimate_for_ai" in failures

    off_schema = {**REPLY, "first_action": "short", "surprise": 1}
    failures = attempt_from_stream(case, _stream(json.dumps(off_schema))).failures
    assert any(f.startswith("schema $.first_action") for f in failures)
    assert any("surprise" in f for f in failures)
    assert not any(f.startswith("rule ") for f in failures)  # rules only on a valid reply

    wrong_id = {**REPLY, "task_id": "01950000-0000-7000-8000-000000000999"}
    failures = attempt_from_stream(case, _stream(json.dumps(wrong_id))).failures
    assert any(f.startswith("$.task_id:") for f in failures)


def test_runs_cannot_be_lowered(tmp_path: Path, case: Case) -> None:
    toml = tmp_path / "harness.toml"
    toml.write_text(
        'runs = 2\nmodel = "m"\nprovider = "p"\ntimeout_s = 180\ninstall_prefix = "t"\n'
    )
    with pytest.raises(HarnessError, match="runs"):
        load_config(toml)
    with pytest.raises(HarnessError, match="runs"):
        run_case(case, lambda c, n: Attempt(outcome="pass"), runs=1)


def test_the_real_runner_refuses_an_unpinned_model() -> None:
    config = load_config()
    assert config.model.startswith("UNSET")  # until Scott pins the homelab model
    with pytest.raises(HarnessError, match="pin"):
        HermesRunner(config, sha8="0123abcd")


def test_an_attempt_passes_only_without_failures() -> None:
    with pytest.raises(ValueError, match="no failures"):
        Attempt(outcome="pass", failures=("x",))
    with pytest.raises(ValueError, match="outcome"):
        Attempt(outcome="skipped")


def test_an_expected_failure_case_is_strict(tmp_path: Path) -> None:
    meta = {"test_id": "T-X", "req": ["FR-5.2"], "wp": "P1-05", "xfail": "spec:P1-05"}
    waiting = load_case(write_case(tmp_path, meta=meta))
    failed = CaseResult(waiting, (Attempt("pass"), Attempt("fail", ("boom",)), Attempt("pass")))
    passed = CaseResult(waiting, (Attempt("pass"),) * 3)

    assert verdict(failed) == "xfailed"
    assert verdict(passed) == "xpassed"
    assert summary_line(failed).startswith(f"{waiting.id}: xfailed (2/3)")

    suite = ET.fromstring(junit_xml([failed, passed]))  # noqa: S314  # our own report
    assert suite.get("failures") == "1"  # the unexpected pass, nothing else
    assert suite.find("testcase/failure") is not None
