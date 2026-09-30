"""Running a skill case (P1-05): three attempts that must all pass, and tool calls captured
from Hermes' stream-json output checked against the case's allow list."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from harness.tests._cases import case_doc, write_case

RECORDINGS = Path(__file__).parent / "recordings"


@pytest.mark.req("Quality: Hermes skills")
@pytest.mark.wp("P1-05")
@pytest.mark.xfail(strict=True, reason="spec:P1-05")
def test_three_of_three_rule(tmp_path: Path) -> None:
    """T-P1-05-07
    Given a stub attempt runner returning pass, pass, fail, when run_case executes, the
    case is failed with attempts [pass, pass, fail], and the report shows 2/3 with every
    attempt recorded, so a 2-of-3 case reads as flaky and failed.
    """
    from harness.cases import Case, load_case
    from harness.report import junit_xml, summary_line
    from harness.run import Attempt, load_config, run_case

    config = load_config()
    assert config.runs == 3
    case = load_case(write_case(tmp_path))

    outcomes = iter(["pass", "pass", "fail"])
    seen: list[int] = []

    def stub(c: Case, number: int) -> Attempt:
        assert c is case
        seen.append(number)
        outcome = next(outcomes)
        failures = () if outcome == "pass" else ("$.task_id: expected the input's task id",)
        return Attempt(outcome=outcome, failures=failures)

    result = run_case(case, stub, runs=config.runs)

    assert seen == [1, 2, 3]
    assert result.status == "failed"
    assert [a.outcome for a in result.attempts] == ["pass", "pass", "fail"]
    assert "2/3" in summary_line(result)

    suite = ET.fromstring(junit_xml([result]))  # noqa: S314  # the harness's own report
    testcases = [t for t in suite.iter("testcase") if t.get("classname") == f"skills.{case.id}"]
    by_name = {t.get("name"): t for t in testcases}
    assert set(by_name) == {case.id, "attempt 1", "attempt 2", "attempt 3"}
    case_failure = by_name[case.id].find("failure")
    assert case_failure is not None
    assert "2/3" in (case_failure.get("message") or "")
    assert [by_name[f"attempt {n}"].find("failure") is not None for n in (1, 2, 3)] == [
        False,
        False,
        True,
    ]


@pytest.mark.req("Quality: Hermes skills")
@pytest.mark.wp("P1-05")
@pytest.mark.xfail(strict=True, reason="spec:P1-05")
def test_unlisted_tool_call_fails_case(tmp_path: Path) -> None:
    """T-P1-05-08
    A recorded stream-json run with one `tool_use` and an otherwise valid enrichment
    result fails a case whose allow list is empty, naming the tool; the same stream passes
    when the allow list has a glob matching it.
    """
    from harness.cases import load_case
    from harness.run import attempt_from_stream

    stream = (RECORDINGS / "enrich_one_tool_use.jsonl").read_text()

    strict = load_case(write_case(tmp_path, "strict.yaml"))
    attempt = attempt_from_stream(strict, stream)
    assert [call.name for call in attempt.tool_calls] == ["mcp_jev_ask"]
    assert attempt.outcome == "fail"
    assert any("mcp_jev_ask" in failure for failure in attempt.failures)

    allowed = load_case(
        write_case(
            tmp_path,
            "allowed.yaml",
            expect={**case_doc()["expect"], "tool_calls": {"allow": ["mcp_jev_*"]}},
        )
    )
    passing = attempt_from_stream(allowed, stream)
    assert passing.failures == ()
    assert passing.outcome == "pass"
    assert passing.output is not None
    assert passing.output["estimate_minutes"] == 20
