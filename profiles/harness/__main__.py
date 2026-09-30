"""The skill harness command line (P1-05, P2-11).

    uv run python -m harness check --cases tests/cases            # load every case; any machine
    uv run python -m harness run --cases tests/cases [--case ID] [--junit PATH]   # homelab only
    uv run python -m harness run --suite hostile [--runs 3] [--smoke --base SHA]  # homelab only
    uv run python -m harness coverage --suite hostile             # every skill covered; any machine
    uv run python -m harness index --suite hostile [--check]      # the Skills job's case index

`run` installs the profiles the cases need into the runner's Hermes, runs every case
3 times with the pinned model from harness.toml, and exits 1 when a case fails or a case
marked as an expected failure passes. `run --suite hostile` runs every hostile case and
its benign twin against every skill (with `--smoke`, the index's smoke subset plus the
cases of skills and cases changed since `--base`), with the recording mock MCP servers
in place of the profiles' own, and exits 1 unless every run passes the judge.
"""

import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from harness import REPO
from harness.cases import CaseError, load_cases
from harness.hostile import (
    INDEX,
    changed,
    coverage_gaps,
    discover_skills,
    expand,
    load_hostile,
    render_index,
)
from harness.hostile_run import HostileRunner, run_suite, summary
from harness.judge import suite_verdict
from harness.report import OK, junit_xml, summary_line, verdict
from harness.run import (
    MIN_RUNS,
    Attempt,
    CaseResult,
    HarnessError,
    HermesRunner,
    load_config,
    run_case,
)

SUITES = ("hostile",)


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m harness", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="load every case without running a model")
    check.add_argument("--cases", type=Path, default=Path("tests/cases"))
    run = commands.add_parser("run", help="run the cases through the real Hermes")
    run.add_argument("--cases", type=Path, default=Path("tests/cases"))
    run.add_argument("--case", action="append", default=[], help="only this case id")
    run.add_argument("--junit", type=Path, help="write a JUnit report here")
    run.add_argument("--suite", choices=SUITES, help="run a suite instead of the cases")
    run.add_argument("--runs", type=int, help=f"runs per case (at least {MIN_RUNS})")
    run.add_argument("--smoke", action="store_true", help="hostile: the PR subset only")
    run.add_argument("--base", help="hostile, with --smoke: add what changed since this commit")
    coverage = commands.add_parser("coverage", help="check every skill is covered")
    coverage.add_argument("--suite", choices=SUITES, required=True)
    index = commands.add_parser("index", help="write the suite's generated case index")
    index.add_argument("--suite", choices=SUITES, required=True)
    index.add_argument("--check", action="store_true", help="exit 1 if the index is stale")
    return parser.parse_args(argv)


def _changed_paths(base: str) -> list[str]:
    done = subprocess.run(  # noqa: S603  # fixed argv, no shell
        ["git", "diff", "--name-only", f"{base}...HEAD"],  # noqa: S607  # git from PATH, as in CI
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise HarnessError(f"git diff against {base} failed: {done.stderr[-300:]}")
    return done.stdout.split()


def _hostile(args: argparse.Namespace) -> int:
    hostile, skills = load_hostile(), discover_skills()
    if gaps := coverage_gaps(hostile, skills):
        raise HarnessError("hostile coverage gaps: " + "; ".join(gaps))
    touched_skills, touched_cases = (
        changed(_changed_paths(args.base), hostile, skills) if args.base else (set(), set())
    )
    runs = expand(
        hostile,
        skills,
        smoke=args.smoke,
        changed_skills=touched_skills,
        changed_cases=touched_cases,
    )
    config = load_config()
    runs_each = args.runs if args.runs is not None else config.runs
    runner = HostileRunner(config)
    with runner.profiles({run.base.profile for run in runs}):
        results = run_suite(
            runs, runner.run_once, runs_each=runs_each, parallel=config.max_parallel
        )
    for result in results:
        print(summary(result))
    if args.junit is not None:
        reports = [
            CaseResult(
                run.harness_case,
                tuple(Attempt("pass" if v.passed else "fail", v.failures) for v in r.verdicts),
            )
            for run, r in zip(runs, results, strict=True)
        ]
        args.junit.write_text(junit_xml(reports), encoding="utf-8")
    overall = suite_verdict(results)
    print(f"hostile suite: {'passed' if overall.passed else 'failed'} ({len(results)} runs)")
    return 0 if overall.passed else 1


def _cases(args: argparse.Namespace) -> int:
    cases = load_cases(args.cases)
    if args.command == "check":
        print(f"{len(cases)} cases load")
        return 0
    if args.case:
        unknown = sorted(set(args.case) - {c.id for c in cases})
        if unknown:
            raise CaseError(f"unknown case ids {unknown}")
        cases = [c for c in cases if c.id in args.case]
    config = load_config()
    runs = args.runs if args.runs is not None else config.runs
    runner = HermesRunner(config)
    with (
        runner.profiles({c.profile for c in cases}),
        ThreadPoolExecutor(max_workers=config.max_parallel) as pool,
    ):
        results = list(pool.map(lambda case: run_case(case, runner.attempt, runs=runs), cases))
    for result in results:
        print(summary_line(result))
    if args.junit is not None:
        args.junit.write_text(junit_xml(results), encoding="utf-8")
    return 0 if all(verdict(r) in OK for r in results) else 1


def main(argv: list[str]) -> int:
    args = _parse(argv)
    try:
        if args.command == "coverage":
            gaps = coverage_gaps(load_hostile(), discover_skills())
            for gap in gaps:
                print(gap)
            print(f"hostile coverage: {len(gaps)} gaps")
            return 1 if gaps else 0
        if args.command == "index":
            text = render_index(load_hostile(), discover_skills())
            if args.check:
                current = INDEX.read_text(encoding="utf-8") if INDEX.is_file() else ""
                stale = current != text
                print(f"{INDEX.relative_to(REPO)}: {'stale' if stale else 'current'}")
                return 1 if stale else 0
            INDEX.parent.mkdir(parents=True, exist_ok=True)
            INDEX.write_text(text, encoding="utf-8")
            print(f"wrote {INDEX.relative_to(REPO)}")
            return 0
        if getattr(args, "runs", None) is not None and args.runs < MIN_RUNS:
            raise HarnessError(f"--runs is {args.runs}; every case runs {MIN_RUNS} times")
        if args.command == "run" and args.suite == "hostile":
            return _hostile(args)
        return _cases(args)
    except (CaseError, HarnessError) as exc:
        print(f"harness: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
