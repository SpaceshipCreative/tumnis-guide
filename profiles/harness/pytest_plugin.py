"""Skill cases as pytest items (P1-05): every `*.yaml` under a `cases` directory is one
item per case (`tests/cases/enrich/hybrid_invoice.yaml::enrich-hybrid-invoice`), tagged
from the case's `meta` block (`req`, `wp`, and a strict expected failure for `xfail`), so
the traceability job sees the skill spec tests like any other test.

The items need the real Hermes and the pinned model, so they skip unless pytest runs with
`--run-skills` (the Skills job on the homelab runner).
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from harness.cases import Case, load_case
from harness.report import summary_line
from harness.run import HermesRunner, load_config, run_case

_RUNNER = pytest.StashKey[HermesRunner]()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-skills",
        action="store_true",
        default=False,
        help="run the skill cases through the real Hermes (homelab runner only)",
    )


def pytest_collect_file(parent: pytest.Collector, file_path: Path) -> pytest.Collector | None:
    if file_path.suffix == ".yaml" and "cases" in file_path.parent.parts:
        collector: CaseFile = CaseFile.from_parent(parent, path=file_path)
        return collector
    return None


class CaseFile(pytest.File):
    def collect(self) -> Iterator[pytest.Item]:
        case = load_case(self.path)
        yield CaseItem.from_parent(self, name=case.id, case=case)


class CaseItem(pytest.Item):
    def __init__(self, *, case: Case, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.case = case
        self.test_id = case.meta.test_id  # what a test function's first docstring line is
        if case.meta.req:
            self.add_marker(pytest.mark.req(*case.meta.req))
        if case.meta.wp:
            self.add_marker(pytest.mark.wp(case.meta.wp))
        if case.meta.xfail:
            self.add_marker(pytest.mark.xfail(strict=True, reason=case.meta.xfail))

    def _runner(self) -> HermesRunner:
        stash = self.config.stash
        if _RUNNER not in stash:
            stash[_RUNNER] = HermesRunner(load_config())
            self.config.add_cleanup(stash[_RUNNER].delete_all)
        runner = stash[_RUNNER]
        if self.case.profile not in runner.installed:
            runner.install(self.case.profile)
        return runner

    def runtest(self) -> None:
        if not self.config.getoption("--run-skills"):
            pytest.skip("skill cases run on the homelab runner (--run-skills)")
        runner = self._runner()
        result = run_case(self.case, runner.attempt, runs=runner.config.runs)
        if result.status != "passed":
            pytest.fail(summary_line(result), pytrace=False)

    def reportinfo(self) -> tuple[Path, int, str]:
        return self.path, 0, f"skill case {self.case.id}"
