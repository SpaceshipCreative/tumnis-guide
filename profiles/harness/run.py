"""Running a skill case (P1-05): every attempt through the daemon's own run path, three
attempts per case, all of which must pass.

An attempt is judged from Hermes' stream-json output exactly as production reads it: the
daemon's `read_stream_json` and `build_result` turn the stream into the reply (so a reply
with prose around the JSON fails as `no_json` here too). Then every tool call must match the
case's allow list, the reply must validate against the committed JSON Schema, and the
case's named rules and JSON checks must pass.

`run_case` takes the attempt runner as an argument: the unit tests pass a stub, and the
homelab runner passes `HermesRunner.attempt`, which spawns the real Hermes.
"""

import fnmatch
import json
import os
import signal
import subprocess
import tempfile
import time
import tomllib
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Final, Literal
from uuid import UUID

from jsonschema import Draft202012Validator

from harness import REPO
from harness.assertions import RULES, check_json
from harness.cases import Case
from tumnis_daemon.config import DaemonConfig
from tumnis_daemon.protocol import Run, SchemaRef, envelope
from tumnis_daemon.runner import (
    build_result,
    clean_env,
    hermes_argv,
    read_stream_json,
    write_query_file,
)

HARNESS_TOML: Final = Path(__file__).with_name("harness.toml")
MIN_RUNS: Final = 3  # the 3-of-3 rule; neither harness.toml nor a case can lower it
UNSET_PREFIX: Final = "UNSET"  # harness.toml's placeholder until the homelab model is pinned
TOOL_RECORDS: Final = frozenset({"tool_use", "tool_call"})

OUTCOMES: Final = frozenset({"pass", "fail"})


@dataclass(frozen=True)
class ToolCall:
    name: str
    input: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Attempt:
    outcome: str  # "pass" or "fail" (OUTCOMES); checked on construction
    failures: tuple[str, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    output: dict[str, Any] | None = None
    duration_ms: int = 0

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(f"an attempt's outcome is one of {sorted(OUTCOMES)}")
        if self.outcome == "pass" and self.failures:
            raise ValueError("a passing attempt has no failures")


@dataclass(frozen=True)
class CaseResult:
    case: Case
    attempts: tuple[Attempt, ...]

    @property
    def passes(self) -> int:
        return sum(a.outcome == "pass" for a in self.attempts)

    @property
    def status(self) -> Literal["passed", "failed"]:
        """Passed only when every attempt passed: 2 of 3 is a flaky case, and failed."""
        ok = bool(self.attempts) and self.passes == len(self.attempts)
        return "passed" if ok else "failed"


@dataclass(frozen=True)
class HarnessConfig:
    runs: int
    model: str
    provider: str
    timeout_s: int
    install_prefix: str
    max_parallel: int
    hermes_bin: str = "hermes"


class HarnessError(RuntimeError):
    """The harness cannot run (configuration, Hermes install); no case was judged."""


AttemptRunner = Callable[[Case, int], Attempt]


def load_config(path: Path = HARNESS_TOML) -> HarnessConfig:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    config = HarnessConfig(
        runs=int(data["runs"]),
        model=str(data["model"]),
        provider=str(data["provider"]),
        timeout_s=int(data["timeout_s"]),
        install_prefix=str(data["install_prefix"]),
        max_parallel=int(data.get("max_parallel", 1)),
        hermes_bin=str(data.get("hermes_bin", "hermes")),
    )
    if config.runs < MIN_RUNS:
        raise HarnessError(f"{path.name}: runs is {config.runs}; every case runs {MIN_RUNS} times")
    if config.timeout_s < 10 or config.max_parallel < 1:  # noqa: PLR2004  # Run.timeout_s >= 10
        raise HarnessError(f"{path.name}: timeout_s >= 10 and max_parallel >= 1")
    return config


def run_case(case: Case, run_attempt: AttemptRunner, *, runs: int) -> CaseResult:
    """Every one of `runs` attempts (numbered from 1), with no early stop, so the report
    shows how flaky a failing case is."""
    if runs < MIN_RUNS:
        raise HarnessError(f"runs is {runs}; every case runs {MIN_RUNS} times")
    return CaseResult(case, tuple(run_attempt(case, n) for n in range(1, runs + 1)))


# --- judging one attempt ---------------------------------------------------------------


def _run_message(case: Case) -> Run:
    """The daemon's Run message for the case's recorded packet."""
    packet = case.packet
    schema = case.output_schema
    return Run(
        **envelope(str(packet.get("correlation_id") or f"case:{case.id}")[:128]),
        run_id=UUID(str(packet["run_id"])),
        profile=case.profile,
        skill=case.skill,
        packet=packet,
        output_schema=SchemaRef(family=schema.family, name=schema.name, version=schema.version),
        timeout_s=max(int(packet.get("timeout_s") or 120), 10),
    )


def _tool_calls(events: list[dict[str, Any]]) -> tuple[ToolCall, ...]:
    """Every tool call in the stream: Hermes' `tool_use` records (`name`, `input`) and
    the `tool_call` form (`name`, `arguments`) the daemon's recordings use."""
    calls = []
    for event in events:
        if event.get("type") in TOOL_RECORDS:
            raw = event.get("input", event.get("arguments"))
            calls.append(
                ToolCall(name=str(event.get("name")), input=raw if isinstance(raw, dict) else {})
            )
    return tuple(calls)


@cache
def validator(schema_path: str) -> Draft202012Validator:
    schema = json.loads((REPO / schema_path).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def _schema_failures(case: Case, output: dict[str, Any]) -> list[str]:
    errors = sorted(
        validator(case.output_schema.path).iter_errors(output), key=lambda e: e.json_path
    )
    return [f"schema {e.json_path}: {e.message}" for e in errors]


def judge(case: Case, output: dict[str, Any] | None, calls: tuple[ToolCall, ...]) -> list[str]:
    """Every failure of one reply: unlisted tool calls, then the schema, then (only for
    a reply the schema accepts) the named rules, and the JSON checks."""
    failures = [
        f"tool call {call.name!r} is not in the allow list {list(case.allow)}"
        for call in calls
        if not any(fnmatch.fnmatchcase(call.name, glob) for glob in case.allow)
    ]
    if output is None:
        return failures
    schema = _schema_failures(case, output)
    failures += schema
    if not schema:
        body = case.packet["body"]
        failures += [
            f"rule {name}: {code}" for name in case.rules for code in RULES[name](body, output)
        ]
    failures += check_json(output, case.json_checks, case.packet)
    return failures


def attempt_from_stream(
    case: Case,
    stream_text: str,
    *,
    exit_code: int | None = 0,
    timed_out: bool = False,
    duration_ms: int = 0,
) -> Attempt:
    """Judge one attempt from Hermes' stream-json output."""
    events, final = read_stream_json(stream_text)
    result = build_result(
        _run_message(case),
        events,
        final,
        exit_code=exit_code,
        timed_out=timed_out,
        duration_ms=duration_ms,
    )
    calls = _tool_calls(events)
    failures = judge(case, result.output_json, calls)
    if result.status != "succeeded":
        failures.insert(0, f"run {result.status}: {(result.error or '')[:200]}")
    return Attempt(
        outcome="fail" if failures else "pass",
        failures=tuple(failures),
        tool_calls=calls,
        output=result.output_json,
        duration_ms=result.duration_ms,
    )


# --- the real Hermes (homelab runner only) --------------------------------------------


def _kill_group(pid: int) -> None:
    with suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGKILL)


def git_sha8(repo: Path = REPO) -> str:
    sha = os.environ.get("GITHUB_SHA")
    if not sha:
        sha = subprocess.run(  # fixed argv, no shell
            ["git", "rev-parse", "HEAD"],  # noqa: S607  # git from PATH, as in CI
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    return sha[:8]


class HermesRunner:
    """Runs attempts through the real Hermes: the case's profile is installed from the
    repo as `<install_prefix>-<profile>-<sha8>`, and each attempt is a fresh one-shot
    session (no --resume) with the pinned model from harness.toml."""

    def __init__(self, config: HarnessConfig, *, sha8: str | None = None) -> None:
        if config.model.startswith(UNSET_PREFIX) or config.provider.startswith(UNSET_PREFIX):
            raise HarnessError("harness.toml: pin the homelab model and provider first")
        self.config = config
        self.sha8 = sha8 or git_sha8()
        self.installed: dict[str, str] = {}
        self._work = Path(tempfile.mkdtemp(prefix="tumnis-skills-"))
        self._daemon = DaemonConfig(
            server_url="",
            runner_name="skills-harness",
            token_file=Path(os.devnull),
            state_dir=self._work,
            hermes_bin=config.hermes_bin,
        )

    def installed_name(self, profile: str) -> str:
        return f"{self.config.install_prefix}-{profile}-{self.sha8}"

    def _hermes(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603  # fixed argv, no shell
            [self.config.hermes_bin, *args],
            capture_output=True,
            text=True,
            check=False,
            env=clean_env(self._daemon),
            timeout=self.config.timeout_s,
        )

    def install(self, profile: str, *, source: Path | None = None, slot: str | None = None) -> str:
        """Install `profile` from the repo (or from `source`, a prepared copy of it); a
        `slot` installs one more copy under its own name (`...-<slot>`), keyed the same
        way in `installed`."""
        key = profile if slot is None else f"{profile}-{slot}"
        name = self.installed_name(key)
        source = source or REPO / "profiles" / profile
        done = self._hermes("profile", "install", str(source), "--name", name, "-y")
        if done.returncode != 0:
            raise HarnessError(f"hermes profile install {profile} failed: {done.stderr[-500:]}")
        self.installed[key] = name
        return name

    def delete_all(self) -> None:
        for profile, name in list(self.installed.items()):
            with suppress(OSError, subprocess.TimeoutExpired):
                self._hermes("profile", "delete", name, "-y")
            del self.installed[profile]

    @contextmanager
    def profiles(self, names: set[str]) -> Iterator["HermesRunner"]:
        """Install every profile in `names`, and delete them afterwards, whatever happens."""
        try:
            for name in sorted(names):
                self.install(name)
            yield self
        finally:
            self.delete_all()

    def attempt(self, case: Case, number: int, *, slot: str | None = None) -> Attempt:
        key = case.profile if slot is None else f"{case.profile}-{slot}"
        msg = _run_message(case).model_copy(update={"profile": self.installed[key]})
        run_dir = self._work / "runs" / f"{case.id}-{number}" / (slot or "")
        query = write_query_file(run_dir, str(case.packet["prompt_text"]))
        argv = [
            *hermes_argv(self._daemon, msg, query),
            *("-m", self.config.model, "--provider", self.config.provider),
        ]
        started = time.monotonic()
        proc = subprocess.Popen(  # noqa: S603  # the daemon's fixed argv, no shell
            argv,
            cwd=run_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=clean_env(self._daemon),
            start_new_session=True,
            text=True,
        )
        timed_out = False
        try:
            stdout, _ = proc.communicate(timeout=self.config.timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(proc.pid)
            stdout, _ = proc.communicate()
        finally:
            if proc.poll() is None:
                _kill_group(proc.pid)
                proc.wait()
        return attempt_from_stream(
            case,
            stdout or "",
            exit_code=None if timed_out else proc.returncode,
            timed_out=timed_out,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
