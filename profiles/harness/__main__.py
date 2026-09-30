"""The skill harness command line (P1-05).

    uv run python -m harness check --cases tests/cases            # load every case; any machine
    uv run python -m harness run --cases tests/cases [--case ID] [--junit PATH]   # homelab only

`run` installs the profiles the cases need into the runner's Hermes, runs every case
3 times with the pinned model from harness.toml, and exits 1 when a case fails or a case
marked as an expected failure passes.
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from harness.cases import CaseError, load_cases
from harness.report import OK, junit_xml, summary_line, verdict
from harness.run import HarnessError, HermesRunner, load_config, run_case


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m harness", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="load every case without running a model")
    check.add_argument("--cases", type=Path, default=Path("tests/cases"))
    run = commands.add_parser("run", help="run the cases through the real Hermes")
    run.add_argument("--cases", type=Path, default=Path("tests/cases"))
    run.add_argument("--case", action="append", default=[], help="only this case id")
    run.add_argument("--junit", type=Path, help="write a JUnit report here")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse(argv)
    try:
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
        runner = HermesRunner(config)
        with (
            runner.profiles({c.profile for c in cases}),
            ThreadPoolExecutor(max_workers=config.max_parallel) as pool,
        ):
            results = list(
                pool.map(lambda case: run_case(case, runner.attempt, runs=config.runs), cases)
            )
    except (CaseError, HarnessError) as exc:
        print(f"harness: {exc}", file=sys.stderr)
        return 2
    for result in results:
        print(summary_line(result))
    if args.junit is not None:
        args.junit.write_text(junit_xml(results), encoding="utf-8")
    return 0 if all(verdict(r) in OK for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
