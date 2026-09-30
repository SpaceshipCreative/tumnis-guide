"""Reporting skill case results (P1-05): a summary line per case and a JUnit report that
records every attempt, so a 2-of-3 case reads as flaky and failed.

A case whose `meta.xfail` is set (a spec test waiting for its skill) is a strict expected
failure, as in pytest: failing is `xfailed` (fine), passing is `xpassed` (a failure, so the
marker gets removed).
"""

import xml.etree.ElementTree as ET
from collections.abc import Iterable
from typing import Final, Literal

from harness.run import CaseResult

Verdict = Literal["passed", "failed", "xfailed", "xpassed"]
OK: Final = frozenset({"passed", "xfailed"})


def verdict(result: CaseResult) -> Verdict:
    if result.case.meta.xfail is None:
        return result.status
    return "xpassed" if result.status == "passed" else "xfailed"


def summary_line(result: CaseResult) -> str:
    """For example `enrich-hybrid-invoice: failed (2/3)`, then the first failure of each
    failed attempt."""
    line = f"{result.case.id}: {verdict(result)} ({result.passes}/{len(result.attempts)})"
    details = [
        f"\n  attempt {n}: {attempt.failures[0]}"
        for n, attempt in enumerate(result.attempts, start=1)
        if attempt.failures
    ]
    return line + "".join(details)


def junit_xml(results: Iterable[CaseResult]) -> str:
    """One `skills` suite: per case, a testcase named after the case (failed unless every
    attempt passed) and one testcase per attempt."""
    suite = ET.Element("testsuite", name="skills")
    tests = failures = skipped = 0
    for result in results:
        classname = f"skills.{result.case.id}"
        runs = len(result.attempts)
        case_el = ET.SubElement(suite, "testcase", classname=classname, name=result.case.id)
        tests += 1
        case_verdict = verdict(result)
        match case_verdict:
            case "failed":
                failures += 1
                ET.SubElement(
                    case_el, "failure", message=f"{result.passes}/{runs} attempts passed"
                ).text = summary_line(result)
            case "xpassed":
                failures += 1
                ET.SubElement(
                    case_el,
                    "failure",
                    message=f"unexpectedly passed {runs}/{runs}; remove meta.xfail",
                )
            case "xfailed":
                skipped += 1
                ET.SubElement(
                    case_el,
                    "skipped",
                    message=f"expected failure ({result.case.meta.xfail}): "
                    f"{result.passes}/{runs} attempts passed",
                )
            case "passed":
                pass
        for n, attempt in enumerate(result.attempts, start=1):
            attempt_el = ET.SubElement(
                suite,
                "testcase",
                classname=classname,
                name=f"attempt {n}",
                time=f"{attempt.duration_ms / 1000:.3f}",
            )
            tests += 1
            if attempt.outcome == "pass":
                continue
            first = attempt.failures[0] if attempt.failures else ""
            if case_verdict == "xfailed":  # expected: reported, never a red build
                skipped += 1
                ET.SubElement(attempt_el, "skipped", message=f"expected failure: {first}")
            else:
                failures += 1
                ET.SubElement(attempt_el, "failure", message=first).text = "\n".join(
                    attempt.failures
                )
    suite.set("tests", str(tests))
    suite.set("failures", str(failures))
    suite.set("skipped", str(skipped))
    return ET.tostring(suite, encoding="unicode")
