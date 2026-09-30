"""Running the hostile suite (P2-11, SAF-6, A2.7) through the real Hermes, on the homelab
runner only.

Each profile the runs need is installed once per parallel slot from a prepared copy whose
`mcp.json` runs only the recording mocks (harness.mock_mcp_min for Tumnis,
harness.mock_worker_tools for GitHub, Coolify, Proxmox and Jev), every one writing to the
slot's own JSON-lines file. A slot runs one attempt at a time: the harness empties the
file, runs Hermes on the injected packet in a fresh one-shot session (the daemon's own run
path, as for every skill case), then reads what the mocks recorded. Hermes' own built-in
tool calls (anything not `mcp_*`) come from its stream as server `hermes`. The judge rules
on that transcript; the judge never retries a failed run.

The mocks speak MCP over stdio (a subprocess of Hermes), so they open no socket and can
reach nothing: a hostile run touches no real server.
"""

import json
import queue
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

from harness import REPO
from harness.cases import SchemaName
from harness.hostile import HostileRun
from harness.judge import RunResult, Transcript, Verdict, judge, judge_twin
from harness.mock_mcp_min import RecordedCall, read_records
from harness.mock_worker_tools import WORKER_TOOLS
from harness.run import MIN_RUNS, Attempt, HarnessConfig, HarnessError, HermesRunner

HERMES: Final = "hermes"  # the server name of Hermes' own tool calls in a transcript
MCP_PREFIX: Final = "mcp_"  # Hermes names MCP tools mcp_<server>_<tool> (or mcp__...__)

RunOnce = Callable[[HostileRun, int], Verdict]


def mock_mcp_config(record: Path, python: str = sys.executable) -> dict[str, Any]:
    """The `mcp.json` of a hostile run: every server is a recording mock writing to
    `record`, started with this interpreter (the profiles project's, which has the SDK)."""
    env = {"PYTHONPATH": str(REPO / "profiles")}
    servers: dict[str, Any] = {
        "tumnis": {
            "command": python,
            "args": ["-m", "harness.mock_mcp_min", "--record", str(record)],
            "env": env,
        }
    }
    for server in WORKER_TOOLS:
        servers[server] = {
            "command": python,
            "args": [
                "-m",
                "harness.mock_worker_tools",
                "--server",
                server,
                "--record",
                str(record),
            ],
            "env": env,
        }
    return {"mcpServers": servers}


def prepare_profile(profile: str, target: Path, record: Path) -> Path:
    """A copy of profiles/<profile> under `target` whose mcp.json runs only the mocks."""
    dest = target / profile
    shutil.copytree(REPO / "profiles" / profile, dest)
    config = json.dumps(mock_mcp_config(record), indent=2)
    (dest / "mcp.json").write_text(config + "\n", encoding="utf-8")
    return dest


def transcript_of(
    attempt: Attempt, records: Sequence[RecordedCall], schema: SchemaName
) -> Transcript:
    """The judge's view of one attempt: what the mocks recorded, then Hermes' built-in
    calls from the stream, and the reply (None when the run gave no JSON reply)."""
    built_in = [
        RecordedCall(HERMES, call.name, dict(call.input))
        for call in attempt.tool_calls
        if not call.name.startswith(MCP_PREFIX)
    ]
    return Transcript(
        calls=(*records, *built_in),
        output=attempt.output,
        output_schema=schema,
    )


def verdict_of(run: HostileRun, transcript: Transcript) -> Verdict:
    return (judge if run.kind == "hostile" else judge_twin)(run.case, transcript)


class HostileRunner:
    """Hermes with the recording mocks: `slots` installed copies of each profile, each
    with its own record file, so attempts run in parallel without mixing their calls."""

    def __init__(self, config: HarnessConfig, *, sha8: str | None = None) -> None:
        self.hermes = HermesRunner(config, sha8=sha8)  # refuses an unpinned model
        self.config = config
        self._work = Path(tempfile.mkdtemp(prefix="tumnis-hostile-"))
        self._free: queue.Queue[tuple[str, Path]] = queue.Queue()

    @contextmanager
    def profiles(self, names: set[str]) -> Iterator["HostileRunner"]:
        try:
            for n in range(self.config.max_parallel):
                slot = f"h{n}"
                record = self._work / slot / "calls.jsonl"
                for name in sorted(names):
                    source = prepare_profile(name, self._work / slot, record)
                    self.hermes.install(name, source=source, slot=slot)
                self._free.put((slot, record))
            yield self
        finally:
            self.hermes.delete_all()

    def run_once(self, run: HostileRun, number: int) -> Verdict:
        slot, record = self._free.get()
        try:
            record.write_text("", encoding="utf-8")
            attempt = self.hermes.attempt(run.harness_case, number, slot=slot)
            transcript = transcript_of(attempt, read_records(record), run.base.output_schema)
        finally:
            self._free.put((slot, record))
        return verdict_of(run, transcript)


def run_suite(
    runs: Sequence[HostileRun], run_once: RunOnce, *, runs_each: int, parallel: int = 1
) -> list[RunResult]:
    """Every run `runs_each` times (at least 3), in parallel up to `parallel`."""
    if runs_each < MIN_RUNS:
        raise HarnessError(f"runs is {runs_each}; every case runs {MIN_RUNS} times")
    jobs = [(run, n) for run in runs for n in range(1, runs_each + 1)]
    with ThreadPoolExecutor(max_workers=max(parallel, 1)) as pool:
        verdicts = list(pool.map(lambda job: run_once(*job), jobs))
    return [
        RunResult(run.label, tuple(verdicts[i * runs_each : (i + 1) * runs_each]))
        for i, run in enumerate(runs)
    ]


def summary(result: RunResult) -> str:
    """For example `email-004-fake-approval/enrich: failed (2/3)`, then the first failure
    of each failed run."""
    passes = sum(v.passed for v in result.verdicts)
    status = "passed" if result.verdict.passed else "failed"
    line = f"{result.label}: {status} ({passes}/{len(result.verdicts)})"
    details = [
        f"\n  run {n}: {v.failures[0]}"
        for n, v in enumerate(result.verdicts, start=1)
        if not v.passed and v.failures
    ]
    return line + "".join(details)
