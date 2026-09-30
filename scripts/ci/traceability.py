#!/usr/bin/env python3
"""traceability: requirement-to-test matrix and gate (AGENTS.md TDD rule 6, P0-03).

Every PRD requirement a work package traces is scheduled in the first phase that traces
it. When that phase has `status: done` in docs/plan/work-packages.yaml, the requirement
needs at least one test tagged with it that is not an expected failure. A tag that looks
like a PRD ID but is not in the PRD fails as unknown. Gaps in active and planned phases
are reported, not failed.

    uv run --project backend python scripts/ci/traceability.py --out trace.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _tests_extract import TsBlock, ts_blocks

REPO = Path(__file__).resolve().parents[2]
PRD_ID = re.compile(r"^(J[1-8]|FR-\d+\.\d+|SEC-\d+|REL-\d+|PERF-\d+|SAF-\d+|SAAS-\d+)$")
JOURNEY = re.compile(r"^\*\*(J[1-8])\.")
WP_ID = re.compile(r"^P\d-\d\d$")
BRACKET_TAG = re.compile(r"\[([^\]]+)\]")
VITEST_GLOBS = ("src/**/*.test.ts", "src/**/*.test.tsx")
PLAYWRIGHT_GLOBS = ("e2e/**/*.spec.ts",)


@dataclass
class TestRef:
    id: str  # nodeid or "<file> > <title>"
    reqs: set[str]
    wp: str | None
    expected_failure: bool  # spec xfail, test.fails, test.fail(), or skipped


def prd_ids(prd: Path) -> set[str]:
    """First column of every requirement table, plus the journey headings (**J1. ...)."""
    known: set[str] = set()
    for line in prd.read_text().splitlines():
        if line.startswith("|"):
            first = line.strip("|").split("|", 1)[0].strip()
            if PRD_ID.match(first):
                known.add(first)
        elif match := JOURNEY.match(line):
            known.add(match.group(1))
    return known


def _traces(item: dict[str, Any]) -> list[str]:
    raw = item.get("traces", [])
    parts = raw if isinstance(raw, list) else [raw]
    return [piece.strip() for part in parts for piece in str(part).split(",") if piece.strip()]


def first_phase_by_requirement(plan: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for phase in plan["phases"]:
        wps = [wp for milestone in phase.get("milestones", []) for wp in milestone.get("wps", [])]
        for item in phase.get("acceptance", []) + wps:
            for rid in _traces(item):
                if PRD_ID.match(rid):
                    out[rid] = min(out.get(rid, 99), int(phase["id"]))
    return out


# --- test references ------------------------------------------------------------------


def _load_refs(raw: list[dict[str, Any]]) -> list[TestRef]:
    return [
        TestRef(r["id"], set(r.get("reqs", [])), r.get("wp"), bool(r.get("expected_failure")))
        for r in raw
    ]


def _collect(project: Path, prefix: str) -> list[TestRef]:
    """Collect one pytest project with the pytest_trace plugin; node ids get `prefix`."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "trace.json"
        path = os.pathsep.join(
            filter(None, [str(Path(__file__).parent), os.environ.get("PYTHONPATH")])
        )
        env = {**os.environ, "TRACE_JSON": str(target), "PYTHONPATH": path}
        argv = [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "pytest_trace"]
        result = subprocess.run(  # noqa: S603 (fixed argv, no shell)
            argv, cwd=project, env=env, capture_output=True, text=True, check=False
        )
        if result.returncode != 0 or not target.is_file():
            raise RuntimeError(f"pytest collection failed:\n{result.stdout}{result.stderr}")
        raw = json.loads(target.read_text())
    return _load_refs([{**r, "id": prefix + r["id"]} for r in raw])


def pytest_refs(repo: Path, trace_json: Path | None) -> list[TestRef]:
    """From a trace.json, or by collecting backend/ and profiles/ (the harness tests and
    the skill cases, P1-05) with the pytest_trace plugin."""
    if trace_json is not None:
        return _load_refs(json.loads(trace_json.read_text()))
    refs = _collect(repo / "backend", "")
    if (repo / "profiles" / "conftest.py").is_file():
        refs += _collect(repo / "profiles", "profiles/")
    return refs


def _ts_files(frontend: Path, globs: tuple[str, ...]) -> list[Path]:
    files = {path for pattern in globs for path in frontend.glob(pattern)}
    return sorted(path for path in files if "node_modules" not in path.parts)


def _frontend_blocks(repo: Path) -> dict[str, tuple[str, dict[str, TsBlock]]]:
    """{file: (kind, blocks)} for Vitest and Playwright files, parsed by ts_tests.mjs."""
    frontend = repo / "frontend"
    kinds = dict.fromkeys(_ts_files(frontend, VITEST_GLOBS), "vitest")
    kinds |= dict.fromkeys(_ts_files(frontend, PLAYWRIGHT_GLOBS), "playwright")
    if not kinds:
        return {}
    labels = {path.relative_to(repo).as_posix(): path for path in kinds}
    blocks = ts_blocks({label: path.read_text() for label, path in labels.items()})
    return {label: (kinds[labels[label]], blocks[label]) for label in labels}


def _ids_from(tags: list[str]) -> tuple[set[str], str | None]:
    reqs = {tag.lstrip("@") for tag in tags}
    wps = sorted(r for r in reqs if WP_ID.match(r))
    return reqs - set(wps), (wps[0] if wps else None)


def frontend_refs(repo: Path) -> list[TestRef]:
    """Vitest titles carry `[P0-25][FR-3.10]`; Playwright tests carry `tag: ['@FR-3.3']`.

    Both are read statically with the TypeScript extractor spec-guard uses (no browser,
    no `playwright --list`).
    """
    refs: list[TestRef] = []
    for file, (kind, blocks) in _frontend_blocks(repo).items():
        for key, block in blocks.items():
            title = str(block["title"])
            raw_tags = block["tags"]
            tags = BRACKET_TAG.findall(title) if kind == "vitest" else []
            tags += [str(tag) for tag in raw_tags] if isinstance(raw_tags, list) else []
            reqs, wp = _ids_from(tags)
            proof = not (block["fails"] or block["skip"])
            refs.append(TestRef(f"{file} > {key}", reqs, wp, expected_failure=not proof))
    return refs


# --- report ---------------------------------------------------------------------------


def write_markdown(
    first: dict[str, int],
    statuses: dict[int, str],
    by_req: dict[str, list[TestRef]],
    missing: list[str],
    unknown: dict[str, list[str]],
) -> str:
    def passing(rid: str) -> int:
        return sum(not t.expected_failure for t in by_req.get(rid, []))

    open_gaps = sorted(r for r, p in first.items() if r not in missing and passing(r) == 0)
    lines = [
        "# Traceability",
        "",
        f"{len(first)} requirements scheduled; {len(missing)} missing in done phases; "
        f"{len(unknown)} unknown IDs; {len(open_gaps)} open in active or planned phases.",
        "",
        "## Missing (done phases, fails the build)",
        "",
    ]
    lines += [f"- {r} (phase {first[r]})" for r in missing] or ["None."]
    lines += ["", "## Unknown requirement IDs (fails the build)", ""]
    lines += [f"- {r}: {', '.join(tests)}" for r, tests in sorted(unknown.items())] or ["None."]
    lines += ["", "## Open (active and planned phases, reported only)", ""]
    lines += [
        f"- {r} (phase {first[r]}, {statuses.get(first[r], 'planned')})" for r in open_gaps
    ] or ["None."]
    lines += ["", "## Matrix", "", "| Requirement | Phase | Status | Passing | Expected failures |"]
    lines += ["| --- | --- | --- | --- | --- |"]
    for rid in sorted(first, key=lambda r: (first[r], r)):
        tests = by_req.get(rid, [])
        pending = len(tests) - passing(rid)
        status = statuses.get(first[rid], "planned")
        lines.append(f"| {rid} | {first[rid]} | {status} | {passing(rid)} | {pending} |")
    return "\n".join(lines) + "\n"


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--plan", type=Path, help="default: <repo>/docs/plan/work-packages.yaml")
    parser.add_argument("--prd", type=Path, help="default: <repo>/docs/PRD.md")
    parser.add_argument("--trace-json", type=Path, help="pytest refs; default: collect backend/")
    parser.add_argument("--out", type=Path, default=Path("trace.md"))
    parser.add_argument("--json", type=Path, help="also write the matrix as JSON")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse(argv)
    repo: Path = args.repo
    plan = yaml.safe_load((args.plan or repo / "docs/plan/work-packages.yaml").read_text())
    known = prd_ids(args.prd or repo / "docs/PRD.md")
    first = first_phase_by_requirement(plan)
    statuses = {int(p["id"]): str(p.get("status", "planned")) for p in plan["phases"]}
    done = {pid for pid, status in statuses.items() if status == "done"}

    tests = pytest_refs(repo, args.trace_json) + frontend_refs(repo)
    by_req: dict[str, list[TestRef]] = defaultdict(list)
    unknown: dict[str, list[str]] = defaultdict(list)
    for test in tests:
        for rid in test.reqs:
            by_req[rid].append(test)
            if PRD_ID.match(rid) and rid not in known:
                unknown[rid].append(test.id)
    missing = sorted(
        (r for r, p in first.items() if p in done and all(t.expected_failure for t in by_req[r])),
        key=lambda r: (first[r], r),
    )

    markdown = write_markdown(first, statuses, by_req, missing, unknown)
    args.out.write_text(markdown)
    print(markdown)
    if args.json:
        matrix = {
            "missing": missing,
            "unknown": unknown,
            "requirements": {
                rid: {
                    "phase": first[rid],
                    "tests": [asdict(t) | {"reqs": sorted(t.reqs)} for t in by_req.get(rid, [])],
                }
                for rid in sorted(first)
            },
        }
        args.json.write_text(json.dumps(matrix, indent=1))
    return 1 if (missing or unknown) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
