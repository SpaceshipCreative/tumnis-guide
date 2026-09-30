"""Skill cases as pytest items (P1-05): every `*.yaml` under a `cases` directory is one
item per case (`tests/cases/enrich/hybrid_invoice.yaml::enrich-hybrid-invoice`), tagged
from the case's `meta` block (`req`, `wp`, and a strict expected failure for `xfail`), so
the traceability job sees the skill spec tests like any other test.

The hostile suite's generated index (`tests/cases/hostile/index.yaml`, P2-11) is one item
per (case, skill) instead (`...::email-004-fake-approval[enrich]`), tagged from the
index's `meta` (T-P2-11-01): each runs the case and its benign twin 3 times against the
recording mocks and passes only when the judge passes all of them.

The items need the real Hermes and the pinned model, so they skip unless pytest runs with
`--run-skills` (the Skills job on the homelab runner). So do the test functions listed in
`HOMELAB_TESTS`, which run the hostile suite through the command line (A2.7, P2-00).
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from harness.cases import SUITE_DIR, Case, CaseMeta, is_suite_file, load_case
from harness.report import summary_line
from harness.run import HermesRunner, load_config, run_case

_RUNNER = pytest.StashKey[HermesRunner]()
_HOSTILE = pytest.StashKey[Any]()  # harness.hostile_run.HostileRunner, entered
# Test functions that run the real Hermes themselves (node ids from the profiles project).
HOMELAB_TESTS = frozenset(
    {
        "harness/tests/test_a2_7_hostile_suite.py"
        "::test_no_skill_follows_an_instruction_in_the_hostile_set",
    }
)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-skills",
        action="store_true",
        default=False,
        help="run the skill cases through the real Hermes (homelab runner only)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-skills"):
        return
    skip = pytest.mark.skip(reason="runs the real Hermes on the homelab runner (--run-skills)")
    for item in items:
        if item.nodeid in HOMELAB_TESTS:
            item.add_marker(skip)


def pytest_collect_file(parent: pytest.Collector, file_path: Path) -> pytest.Collector | None:
    if file_path.suffix != ".yaml" or "cases" not in file_path.parent.parts:
        return None
    if is_suite_file(file_path):
        if file_path.parent.name == SUITE_DIR and file_path.name == "index.yaml":
            index: HostileIndex = HostileIndex.from_parent(parent, path=file_path)
            return index
        return None
    collector: CaseFile = CaseFile.from_parent(parent, path=file_path)
    return collector


def _mark(item: pytest.Item, meta: CaseMeta) -> None:
    if meta.req:
        item.add_marker(pytest.mark.req(*meta.req))
    if meta.wp:
        item.add_marker(pytest.mark.wp(meta.wp))
    if meta.xfail:
        item.add_marker(pytest.mark.xfail(strict=True, reason=meta.xfail))


class CaseFile(pytest.File):
    def collect(self) -> Iterator[pytest.Item]:
        case = load_case(self.path)
        yield CaseItem.from_parent(self, name=case.id, case=case)


class CaseItem(pytest.Item):
    def __init__(self, *, case: Case, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.case = case
        self.test_id = case.meta.test_id  # what a test function's first docstring line is
        _mark(self, case.meta)

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


class HostileIndex(pytest.File):
    """The hostile suite's generated index: one item per entry of `runs`."""

    def collect(self) -> Iterator[pytest.Item]:
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        raw = data.get("meta") or {}
        meta = CaseMeta(
            test_id=raw.get("test_id"),
            req=tuple(raw.get("req") or ()),
            wp=raw.get("wp"),
            xfail=raw.get("xfail"),
        )
        for entry in data.get("runs") or []:
            yield HostileItem.from_parent(
                self,
                name=f"{entry['case']}[{entry['skill']}]",
                case_id=str(entry["case"]),
                skill=str(entry["skill"]),
                meta=meta,
            )


class HostileItem(pytest.Item):
    def __init__(self, *, case_id: str, skill: str, meta: CaseMeta, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.case_id = case_id
        self.skill = skill
        self.test_id = meta.test_id
        _mark(self, meta)

    def runtest(self) -> None:
        if not self.config.getoption("--run-skills"):
            pytest.skip("the hostile suite runs on the homelab runner (--run-skills)")
        # The suite's modules load only here: the collection needs none of them.
        from harness.hostile import BASES, discover_skills, expand, load_hostile  # noqa: PLC0415
        from harness.hostile_run import HostileRunner, run_suite, summary  # noqa: PLC0415

        hostile = load_hostile()
        runs = [
            run
            for run in expand(hostile.only(lambda c: c.id == self.case_id), discover_skills())
            if run.base.skill == self.skill
        ]
        stash = self.config.stash
        if _HOSTILE not in stash:
            config = load_config()
            runner = HostileRunner(config)
            entered = runner.profiles({base.profile for base in BASES.values()})
            entered.__enter__()

            def leave() -> None:
                entered.__exit__(None, None, None)

            self.config.add_cleanup(leave)
            stash[_HOSTILE] = runner
        runner = stash[_HOSTILE]
        results = run_suite(
            runs,
            runner.run_once,
            runs_each=runner.config.runs,
            parallel=runner.config.max_parallel,
        )
        failed = [summary(r) for r in results if not r.verdict.passed]
        if failed:
            pytest.fail("\n".join(failed), pytrace=False)

    def reportinfo(self) -> tuple[Path, int, str]:
        return self.path, 0, f"hostile case {self.case_id} on {self.skill}"
