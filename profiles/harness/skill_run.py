"""Running the tool-calling skill cases (P2-12) through the real Hermes, on the homelab
runner only.

Like the hostile suite (harness.hostile_run), each profile is installed once per parallel
slot from a prepared copy whose MCP servers are only mocks writing to the slot's record
file: the full Tumnis mock (harness.mock_mcp) and the memory mock (harness.mock_memory),
both scripted by the case, and the worker-tool mocks (harness.mock_worker_tools). Before
each attempt the harness writes the case's script, empties the record and, for a case
with a fixture repository, copies it into a fresh working directory (a git repository
whose `make test` records each suite result). Hermes runs there with the harness's git
wrapper first on PATH, which records `git push`. Files the run removes from the working
directory are recorded when the Tumnis mock next answers a call, and after the attempt
(harness.workdir). The case is judged on that timeline
and the reply (harness.calls and harness.run.judge).
"""

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from harness import REPO
from harness.cases import REPOS, Case
from harness.hostile_run import write_mock_servers
from harness.mock_mcp_min import RecordedCall, read_records
from harness.mock_worker_tools import WORKER_TOOLS
from harness.run import Attempt, HarnessConfig, HermesRunner
from harness.workdir import deletion_record, files_of, recorded_deletions, write_state

HIDDEN: Final = REPO / "profiles" / "tests" / "fixtures" / "hidden"


def case_mcp_config(
    record: Path, script: Path, state: Path, python: str = sys.executable
) -> dict[str, Any]:
    """The MCP servers of a skill case run: the scripted Tumnis and memory mocks, and the
    worker-tool mocks, every one writing to `record`. The Tumnis mock also watches the
    working directory named in `state` for removed files (harness.workdir)."""
    env = {"PYTHONPATH": str(REPO / "profiles")}

    def server(module: str, *extra: str) -> dict[str, Any]:
        return {
            "command": python,
            "args": ["-m", module, *extra, "--record", str(record)],
            "env": env,
        }

    servers: dict[str, Any] = {
        "tumnis": server("harness.mock_mcp", "--script", str(script), "--workdir-state", str(state))
    }
    for name in WORKER_TOOLS:
        servers[name] = (
            server("harness.mock_memory", "--script", str(script))
            if name == "memory"
            else server("harness.mock_worker_tools", "--server", name)
        )
    return {"mcpServers": servers}


def prepare_case_profile(
    profile: str, target: Path, record: Path, script: Path, state: Path
) -> Path:
    """A copy of profiles/<profile> under `target` whose MCP servers are the case mocks."""
    dest = target / profile
    shutil.copytree(REPO / "profiles" / profile, dest)
    write_mock_servers(dest, case_mcp_config(record, script, state))
    return dest


def write_script(case: Case, path: Path) -> None:
    """The case's `mock` block as the mocks read it."""
    script = {
        "responses": {tool: list(specs) for tool, specs in case.mock.responses.items()},
        "recall": list(case.mock.recall),
    }
    path.write_text(json.dumps(script, indent=2) + "\n", encoding="utf-8")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603  # fixed argv, no shell
        [  # noqa: S607  # git from PATH, as in CI
            "git",
            "-c",
            "user.name=Tumnis harness",
            "-c",
            "user.email=harness@example.invalid",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def prepare_workdir(case: Case, run_dir: Path, record: Path) -> Path | None:
    """The case's working directory: a fresh copy of its fixture repository on `main`,
    committed, its Makefile pointed at `record` (and at the hidden test for a red-suite
    case); None for a case without a repository."""
    if case.mock.repo is None:
        return None
    if run_dir.exists():
        shutil.rmtree(run_dir)
    work = run_dir / "work"
    shutil.copytree(REPOS / case.mock.repo, work)
    extra = str(HIDDEN / case.mock.repo) if case.mock.hidden_failure else ""
    makefile = work / "Makefile"
    text = makefile.read_text(encoding="utf-8")
    for key, value in (
        ("@HARNESS_PYTHON@", sys.executable),
        ("@HARNESS_RECORD@", str(record)),
        ("@HARNESS_EXTRA_TESTS@", extra),
    ):
        text = text.replace(key, value)
    makefile.write_text(text, encoding="utf-8")
    _git(work, "init", "-q", "-b", "main")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "calc as the case starts")
    return work


def write_git_shim(bin_dir: Path, record: Path) -> Path:
    """`bin_dir/git`: the harness's git wrapper (harness.git_shim) for one slot."""
    real = shutil.which("git")
    if real is None:
        raise FileNotFoundError("git is not on PATH")
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "git"
    shim.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        f"sys.path.insert(0, {str(REPO / 'profiles')!r})\n"
        "from harness.git_shim import main\n"
        f"sys.exit(main(sys.argv[1:], record={str(record)!r}, real_git={real!r}))\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return shim


@dataclass(frozen=True)
class Slot:
    name: str
    root: Path

    @property
    def record(self) -> Path:
        return self.root / "calls.jsonl"

    @property
    def script(self) -> Path:
        return self.root / "script.json"

    @property
    def state(self) -> Path:
        return self.root / "workdir.json"

    @property
    def bin(self) -> Path:
        return self.root / "bin"


class SkillRunner:
    """Hermes with the case mocks: `max_parallel` installed copies of each profile, each
    slot with its own record, script and git wrapper."""

    def __init__(self, config: HarnessConfig, *, sha8: str | None = None) -> None:
        self.hermes = HermesRunner(config, sha8=sha8)  # refuses an unpinned model
        self.config = config
        self._work = Path(tempfile.mkdtemp(prefix="tumnis-skill-cases-"))
        self._free: queue.Queue[Slot] = queue.Queue()

    @contextmanager
    def profiles(self, names: set[str]) -> Iterator["SkillRunner"]:
        try:
            for n in range(self.config.max_parallel):
                slot = Slot(f"s{n}", self._work / f"s{n}")
                slot.root.mkdir(parents=True)
                write_git_shim(slot.bin, slot.record)
                for name in sorted(names):
                    source = prepare_case_profile(
                        name, slot.root, slot.record, slot.script, slot.state
                    )
                    self.hermes.install(name, source=source, slot=slot.name)
                self._free.put(slot)
            yield self
        finally:
            self.hermes.delete_all()

    def attempt(self, case: Case, number: int) -> Attempt:
        slot = self._free.get()
        try:
            slot.record.write_text("", encoding="utf-8")
            write_script(case, slot.script)
            workdir = prepare_workdir(case, slot.root / "run", slot.record)
            before = files_of(workdir) if workdir is not None else set()
            write_state(slot.state, workdir, before)

            def timeline() -> Sequence[RecordedCall]:
                records = read_records(slot.record)
                removed = (
                    deletion_record(before, workdir, recorded_deletions(records))
                    if workdir is not None
                    else []
                )
                return [*records, *removed]

            path = f"{slot.bin}{os.pathsep}{os.environ.get('PATH', '')}"
            return self.hermes.attempt(
                case,
                number,
                slot=slot.name,
                workdir=workdir,
                extra_env={"PATH": path},
                read_timeline=timeline,
            )
        finally:
            self._free.put(slot)
