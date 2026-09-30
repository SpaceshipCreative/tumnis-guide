"""Running a skill with Hermes, and the profile health check (P1-04, FR-5.11, R-25).

The daemon never builds a prompt: `packet.prompt_text` goes to a query file byte for byte,
Hermes gets a fixed argv without a shell, and the child's environment carries PATH, HOME,
LANG and `HERMES_*` only, so the device token never reaches it.
"""

import asyncio
import contextlib
import json
import logging
import os
import re
import signal
import subprocess
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal

from tumnis_daemon import health
from tumnis_daemon import worktree as worktree_mod
from tumnis_daemon.config import DaemonConfig
from tumnis_daemon.protocol import (
    ERROR_MAX,
    NAME_RE,
    SKILL_RE,
    TEXT_MAX,
    HealthCheck,
    HealthReport,
    Result,
    ResultV2,
    Run,
    StreamKind,
    code_location_of,
    envelope,
    make_status,
    make_stream,
)

if TYPE_CHECKING:
    from tumnis_daemon.state import StateStore

ENV_KEEP: Final = frozenset({"PATH", "HOME", "LANG"})
KILL_GRACE_S: Final = 10.0  # cancel: SIGTERM, then SIGKILL after this (plan default)
TOOL_RECORDS: Final = frozenset({"tool_call", "tool_use"})
LINE_LIMIT: Final = 8 * 1024 * 1024  # one stream-json record
log = logging.getLogger(__name__)

HEALTH_TIMEOUT_S: Final = 30.0  # each health subcommand (plan default)
# The server waits 30 s for a health report; the token probes get only what is left of
# this budget, counted from the check's start with Hermes's own checks included (P2-10).
REPORT_BUDGET_S: Final = 25.0
_FENCE: Final = re.compile(r"```(?:json)?[ \t]*\n(.*)\n[ \t]*```", re.DOTALL)
_VERSION: Final = re.compile(r"\d+(?:\.\d+)+[0-9A-Za-z.+-]*")
_MCP_NAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


class InvalidProfile(ValueError):  # noqa: N818  # the plan's word
    """A profile or skill name the protocol would never send; refused before any spawn."""


def hermes_argv(cfg: DaemonConfig, msg: Run, query_file: Path) -> list[str]:
    if not re.fullmatch(NAME_RE, msg.profile):
        raise InvalidProfile("invalid profile name")
    if not re.fullmatch(SKILL_RE, msg.skill):
        raise InvalidProfile("invalid skill name")
    return [
        cfg.hermes_bin,
        "-p",
        msg.profile,
        "chat",
        "--query-file",
        str(query_file),
        "-s",
        msg.skill,
        "--format",
        "stream-json",
        "--source",
        "tool",
    ]


def write_query_file(run_dir: Path, prompt_text: str) -> Path:
    """Write the prompt verbatim to `<run_dir>/query.txt` (dir 0700, file 0600)."""
    run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    run_dir.chmod(0o700)
    path = run_dir / "query.txt"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(prompt_text.encode())
    path.chmod(0o600)
    return path


def clean_env(cfg: DaemonConfig, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """PATH, HOME, LANG and HERMES_* only; nothing else of the daemon's environment."""
    del cfg  # the token lives in a file, never in the environment
    source = os.environ if environ is None else environ
    return {k: v for k, v in source.items() if k in ENV_KEEP or k.startswith("HERMES_")}


def extract_json_object(text: str) -> dict[str, Any] | None:
    """The one JSON object a skill replies with: the whole text, or the inside of exactly
    one fenced block. Anything else (prose around it, two objects, an array) is None."""
    body = text.strip()
    fenced = _FENCE.fullmatch(body)
    if fenced is not None:
        body = fenced.group(1).strip()
        if "```" in body:
            return None  # more than one fence
    if not body:
        return None
    try:
        value = json.loads(body)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _parse_line(line: str | bytes) -> dict[str, Any] | None:
    try:
        record = json.loads(line)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def read_stream_json(text: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Hermes' stream-json output: every record (lines that are not a JSON object are
    skipped, as `execute` skips them), and the last `result` record."""
    events = [
        record
        for line in text.splitlines()
        if line.strip() and (record := _parse_line(line)) is not None
    ]
    return events, _final(events)


def read_recording(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """A stream-json transcript on disk: every record, and the last `result` record."""
    return read_stream_json(path.read_text(encoding="utf-8"))


def _final(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    finals = [e for e in events if e.get("type") == "result"]
    return finals[-1] if finals else None


def _tokens(final: dict[str, Any]) -> dict[str, int] | None:
    usage = final.get("usage")
    tokens: Any
    if isinstance(usage, dict):
        tokens = {
            "input": usage.get("input_tokens"),
            "output": usage.get("output_tokens"),
        }
    else:
        tokens = final.get("tokens")
    if not isinstance(tokens, dict):
        return None
    ints = {str(k): v for k, v in tokens.items() if isinstance(v, int) and v >= 0}
    return ints or None


def build_result(
    msg: Run,
    events: list[dict[str, Any]],
    final: dict[str, Any] | None,
    *,
    exit_code: int | None,
    timed_out: bool,
    duration_ms: int,
) -> Result:
    del events  # phase 1 reports the terminal record only; run events arrive in P2-07
    text = ""
    output: dict[str, Any] | None = None
    tokens: dict[str, int] | None = None
    session_id: str | None = None
    if final is not None:
        text = str(final.get("text") or "")
        tokens = _tokens(final)
        sid = final.get("session_id")
        session_id = str(sid)[:256] if sid is not None else None
        if isinstance(final.get("duration_ms"), int) and final["duration_ms"] >= 0:
            duration_ms = final["duration_ms"]

    status: Literal["succeeded", "failed", "timed_out"]
    error: str | None
    if timed_out:
        status, error = "timed_out", "timed_out"
    elif final is None:
        status, error = "failed", "no_result_record"
    elif final.get("is_error") or final.get("subtype", "success") != "success":
        status, error = "failed", text or "hermes_error"
    elif (output := extract_json_object(text)) is None:
        status, error = "failed", "no_json"
    else:
        status, error = "succeeded", None

    return Result(
        **envelope(msg.correlation_id),
        run_id=msg.run_id,
        status=status,
        exit_code=exit_code,
        output_json=output if status == "succeeded" else None,
        text=text[:TEXT_MAX],
        error=error[:ERROR_MAX] if error is not None else None,
        duration_ms=max(duration_ms, 0),
        tokens=tokens,
        hermes_session_id=session_id,
    )


def run_dir_for(cfg: DaemonConfig, msg: Run) -> Path:
    return cfg.state_dir / "runs" / str(msg.run_id)


def as_v2(result: Result, **update: Any) -> ResultV2:
    """The result as a protocol-2 `result` (the outbox renders it for the session)."""
    fields = {**result.model_dump(), **update, "schema_version": 2}
    return ResultV2.model_validate(fields)


OnRecord = Callable[[dict[str, Any]], Awaitable[None]]


async def _stop(proc: asyncio.subprocess.Process, grace_s: float) -> None:
    """SIGTERM to the run's process group, SIGKILL once the grace period runs out."""
    _signal_group(proc.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), grace_s)
    except TimeoutError:
        _signal_group(proc.pid, signal.SIGKILL)
        await proc.wait()


async def execute(
    msg: Run,
    cfg: DaemonConfig,
    *,
    timeout_s: float | None = None,
    cancel: asyncio.Event | None = None,
    kill_grace_s: float | None = None,
    workdir: Path | None = None,
    on_record: OnRecord | None = None,
) -> ResultV2:
    """Run one skill. Hermes runs in its own process group, in `workdir` (the run's
    worktree, query file in its `.tumnis/`) or the run's directory. Each stream-json record
    goes to `on_record` as it arrives. Past the timeout the whole group is killed; on
    `cancel` it gets SIGTERM, then SIGKILL after the grace period (10 s by default), and
    the result is `cancelled`."""
    if workdir is not None:
        query = write_query_file(workdir / ".tumnis", str(msg.packet["prompt_text"]))
    else:
        query = write_query_file(run_dir_for(cfg, msg), str(msg.packet["prompt_text"]))
    argv = hermes_argv(cfg, msg, query)
    loop = asyncio.get_running_loop()
    started = loop.time()
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=workdir or query.parent,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,  # never read: a pipe would only fill memory
        env=clean_env(cfg),
        start_new_session=True,
        limit=LINE_LIMIT,
    )
    stdout = proc.stdout
    assert stdout is not None  # noqa: S101  # PIPE above
    events: list[dict[str, Any]] = []

    async def pump() -> None:
        async for line in stdout:
            if (record := _parse_line(line)) is not None:
                events.append(record)
                if on_record is not None:
                    await on_record(record)
        await proc.wait()

    limit = timeout_s if timeout_s is not None else msg.timeout_s
    grace = kill_grace_s if kill_grace_s is not None else cfg.kill_grace_s
    outcome = await _supervise(proc, asyncio.create_task(pump()), cancel, limit, grace)
    duration_ms = int((loop.time() - started) * 1000)
    result = build_result(
        msg,
        events,
        _final(events),
        exit_code=proc.returncode if outcome == "done" else None,
        timed_out=outcome == "timed_out",
        duration_ms=duration_ms,
    )
    if outcome == "cancelled":
        return as_v2(result, status="cancelled", output_json=None, error="cancelled")
    return as_v2(result)


async def _supervise(
    proc: asyncio.subprocess.Process,
    reading: "asyncio.Task[None]",
    cancel: asyncio.Event | None,
    limit: float,
    grace: float,
) -> Literal["done", "timed_out", "cancelled"]:
    """Wait for the run to end, its time limit, or its cancel switch, whichever is first;
    the process group never outlives this."""
    waiting: set[asyncio.Task[Any]] = {reading}
    cancelled_by = asyncio.create_task(cancel.wait()) if cancel is not None else None
    if cancelled_by is not None:
        waiting.add(cancelled_by)
    outcome: Literal["done", "timed_out", "cancelled"] = "done"
    try:
        done, _ = await asyncio.wait(waiting, timeout=limit, return_when=asyncio.FIRST_COMPLETED)
        if reading in done:
            reading.result()
        elif cancelled_by is not None and cancelled_by in done:
            outcome = "cancelled"
            await _stop(proc, grace)
        else:
            outcome = "timed_out"
            _signal_group(proc.pid, signal.SIGKILL)
            await proc.wait()
    finally:
        if proc.returncode is None:  # cancelled from outside: never leave Hermes running
            _signal_group(proc.pid, signal.SIGKILL)
            await proc.wait()
        if outcome != "done":
            _signal_group(proc.pid, signal.SIGKILL)  # stragglers left in the group
        for task in (reading, cancelled_by):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
    return outcome


def _signal_group(pid: int, sig: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, sig)


def profile_version(cfg: DaemonConfig, profile: str) -> str | None:
    """The profile's version stamp, read from `<hermes_home>/profiles/<profile>` exactly as
    the health report reads it (`VERSION`, else `distribution.yaml`); None when it has none."""
    if not re.fullmatch(NAME_RE, profile):
        return None
    return health.profile_version(health.profile_dir(cfg.hermes_home, profile))


async def touched_files(worktree: Path) -> list[str]:
    """The worktree's changed and new files (`git status --porcelain`), relative to it; the
    daemon's own `.tumnis/` is left out. Symlinks are reported, never followed."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            cwd=worktree,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={**clean_env_git(), "GIT_OPTIONAL_LOCKS": "0"},
        )
        out, _ = await proc.communicate()
    except OSError:
        return []
    if proc.returncode != 0:
        return []
    paths: list[str] = []
    entries = out.decode("utf-8", errors="replace").split("\0")
    skip = False
    for entry in entries:
        if skip:  # the source of a rename or copy
            skip = False
            continue
        status, sep, path = entry[:2], entry[2:3], entry[3:]
        if sep != " " or not path:
            continue
        skip = status[0] in "RC"
        if not path.startswith(".tumnis/"):
            paths.append(path)
    return paths


def clean_env_git() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k in ENV_KEEP}


class _RunStream:
    """Turns a run's stream-json records into `stream` messages: assistant text is `log`,
    a tool call is `tool_call`, then each newly touched file in the worktree is
    `file_touched`; `seq` counts from 1 per run."""

    def __init__(self, msg: Run, state: "StateStore", worktree: Path | None) -> None:
        self.msg = msg
        self.state = state
        self.worktree = worktree
        self.seq = 0
        self.touched: set[str] = set()

    async def line(self, kind: StreamKind, text: str) -> None:
        self.seq += 1
        message = make_stream(self.msg.run_id, self.msg.correlation_id, self.seq, kind, text)
        await self.state.send_reliably(message)

    async def record(self, record: dict[str, Any]) -> None:
        kind = record.get("type")
        if kind == "assistant":
            text = str(record.get("text") or "")
            if text:
                await self.line("log", text)
        elif kind in TOOL_RECORDS:
            call = {
                "name": record.get("name"),
                "arguments": record.get("arguments", record.get("input")),
            }
            await self.line("tool_call", json.dumps(call, ensure_ascii=False))
            await self.files()

    async def files(self) -> None:
        if self.worktree is None:
            return
        for path in await touched_files(self.worktree):
            if path not in self.touched:
                self.touched.add(path)
                await self.line("file_touched", path)


async def run_skill(msg: Run, state: "StateStore", cfg: DaemonConfig) -> None:
    """Execute and keep the result until the server acks it. On protocol 2 the run also
    reports `status` (`started` with the profile's VERSION, `cancelling`) and its output as
    `stream` lines. A `worktree` run gets its worktree first and loses it once the result
    is in the outbox, whatever the outcome."""
    state.running.add(msg.run_id)
    cancel = state.cancel_switch(msg.run_id)
    worktree: Path | None = None

    async def announce_cancel() -> None:
        await cancel.wait()
        await state.send_reliably(
            make_status(
                msg.run_id,
                msg.correlation_id,
                "cancelling",
                profile=msg.profile if re.fullmatch(NAME_RE, msg.profile) else None,
                detail=state.cancel_reason(msg.run_id),
            )
        )

    announcing = asyncio.create_task(announce_cancel())
    try:
        result: ResultV2
        try:
            valid_profile = re.fullmatch(NAME_RE, msg.profile) is not None
            await state.send_reliably(
                make_status(
                    msg.run_id,
                    msg.correlation_id,
                    "started",
                    profile=msg.profile if valid_profile else None,
                    profile_version=profile_version(cfg, msg.profile),
                )
            )
            if msg.workdir_policy == "worktree":
                location = code_location_of(msg.packet)
                if location is not None:
                    worktree = await asyncio.to_thread(
                        worktree_mod.prepare, cfg, str(msg.run_id), location
                    )
            stream = _RunStream(msg, state, worktree)
            result = await execute(
                msg, cfg, cancel=cancel, workdir=worktree, on_record=stream.record
            )
            await stream.files()
        except InvalidProfile as exc:
            result = _failed(msg, str(exc))
        except worktree_mod.WorktreeRefused as exc:
            result = _failed(msg, f"worktree_refused: {exc}")
        except (OSError, KeyError, ValueError, subprocess.SubprocessError) as exc:
            # Hermes missing or not executable, no prompt, an overlong stream-json line, a
            # git failure: a failed Result now, not a silent wait for the server's run
            # timeout. Only the error kind is kept, never its message (it can quote the
            # prompt).
            kind = type(exc).__name__
            log.warning("run_failed", extra={"run_id": str(msg.run_id), "kind": kind})
            result = _failed(msg, f"daemon_error:{kind}")
        if cancel.is_set():
            await announcing  # `cancelling` goes before the `cancelled` result
        await state.send_reliably(result)
    finally:
        if not announcing.done():
            announcing.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await announcing
        if worktree is not None:
            await asyncio.to_thread(worktree_mod.remove, cfg, str(msg.run_id))
        state.release(msg.run_id)
        state.running.discard(msg.run_id)


def _failed(msg: Run, error: str) -> ResultV2:
    result = build_result(msg, [], None, exit_code=None, timed_out=False, duration_ms=0)
    return as_v2(result, error=error[:ERROR_MAX])


# --- health check ------------------------------------------------------------------------


async def _hermes(
    cfg: DaemonConfig, *args: str, timeout_s: float = HEALTH_TIMEOUT_S
) -> tuple[int, str] | None:
    """Run one Hermes subcommand; None when it cannot be run or does not finish in time."""
    try:
        proc = await asyncio.create_subprocess_exec(
            cfg.hermes_bin,
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=clean_env(cfg),
            start_new_session=True,
        )
    except OSError:
        return None
    try:
        async with asyncio.timeout(timeout_s):
            out, _ = await proc.communicate()
    except TimeoutError:
        _signal_group(proc.pid, signal.SIGKILL)
        await proc.wait()
        return None
    return proc.returncode or 0, out.decode(errors="replace")


def parse_version(output: str) -> str | None:
    found = _VERSION.search(output)
    return found.group(0)[:64] if found else None


def parse_mcp_list(output: str) -> list[str]:
    """First column of each row; header and decoration rows are skipped (best effort)."""
    names: list[str] = []
    for line in output.splitlines():
        cols = line.split()
        if not cols or not _MCP_NAME.fullmatch(cols[0]) or cols[0].isupper():
            continue
        if cols[0].lower() in {"name", "server", "servers"}:
            continue
        names.append(cols[0])
    return names[:200]


async def hermes_version(cfg: DaemonConfig) -> str | None:
    answered = await _hermes(cfg, "version")
    return parse_version(answered[1]) if answered and answered[0] == 0 else None


async def check_health(msg: HealthCheck, state: "StateStore", cfg: DaemonConfig) -> None:
    report = await health_report(msg, cfg)
    await state.send_reliably(report)


async def health_report(msg: HealthCheck, cfg: DaemonConfig) -> HealthReport:
    started = asyncio.get_running_loop().time()
    if not re.fullmatch(NAME_RE, msg.profile):
        return _report(msg, exists=False, reachable=False, error="invalid profile name")
    version = await _hermes(cfg, "version")
    if version is None:
        return _report(msg, exists=False, reachable=False, error="hermes did not run")
    shown = await _hermes(cfg, "profile", "show", msg.profile)
    exists = shown is not None and shown[0] == 0
    mcp: list[str] = []
    authenticated: bool | None = None
    probed: dict[str, Any] = {}
    if exists:
        listed = await _hermes(cfg, "-p", msg.profile, "mcp", "list")
        if listed is not None and listed[0] == 0:
            mcp = parse_mcp_list(listed[1])
        status = await _hermes(cfg, "-p", msg.profile, "status")
        authenticated = None if status is None else status[0] == 0
        left = REPORT_BUDGET_S - (asyncio.get_running_loop().time() - started)
        probed = await _probe(msg, cfg, deadline_s=max(0.0, min(health.PROBE_DEADLINE_S, left)))
        if not mcp:
            mcp = [server.name for server in probed["mcp_server_details"]]
    return _report(
        msg,
        exists=exists,
        reachable=True,
        authenticated=authenticated,
        version=parse_version(version[1]) if version[0] == 0 else None,
        mcp=mcp,
        error=None if exists else "profile not found",
        **probed,
    )


async def _probe(msg: HealthCheck, cfg: DaemonConfig, *, deadline_s: float) -> dict[str, Any]:
    """P2-10: the profile's MCP servers, its version stamp and its tokens' reach, read and
    probed from this host (each token goes only to its own provider, never to Tumnis)."""
    profile = health.profile_dir(cfg.hermes_home, msg.profile)
    github, coolify = await health.token_reach(
        profile,
        own_repos=msg.own_repos,
        foreign_repos=msg.foreign_repos,
        own_apps=msg.own_apps,
        foreign_apps=msg.foreign_apps,
        coolify_base_url=msg.coolify_base_url,
        deadline_s=deadline_s,
    )
    return {
        "mcp_server_details": health.mcp_servers(profile),
        "profile_version": health.profile_version(profile),
        "github": github,
        "coolify": coolify,
    }


def _report(
    msg: HealthCheck,
    *,
    exists: bool,
    reachable: bool,
    authenticated: bool | None = None,
    version: str | None = None,
    mcp: list[str] | None = None,
    error: str | None = None,
    **probed: Any,
) -> HealthReport:
    return HealthReport(
        **envelope(msg.correlation_id),
        request_id=msg.request_id,
        profile=msg.profile if re.fullmatch(NAME_RE, msg.profile) else "invalid",
        profile_exists=exists,
        reachable=reachable,
        authenticated=authenticated,
        hermes_version=version,
        mcp_servers=mcp or [],
        error=error,
        **probed,
    )
