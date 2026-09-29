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
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal

from tumnis_daemon.config import DaemonConfig
from tumnis_daemon.protocol import (
    ERROR_MAX,
    NAME_RE,
    SKILL_RE,
    TEXT_MAX,
    HealthCheck,
    HealthReport,
    Result,
    Run,
    envelope,
)

if TYPE_CHECKING:
    from tumnis_daemon.state import StateStore

ENV_KEEP: Final = frozenset({"PATH", "HOME", "LANG"})
LINE_LIMIT: Final = 8 * 1024 * 1024  # one stream-json record
log = logging.getLogger(__name__)

HEALTH_TIMEOUT_S: Final = 30.0  # each health subcommand (plan default)
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


def read_recording(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """A stream-json transcript: every record, and the last `result` record."""
    events = [
        record
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and (record := _parse_line(line)) is not None
    ]
    return events, _final(events)


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


async def execute(msg: Run, cfg: DaemonConfig, *, timeout_s: float | None = None) -> Result:
    """Run one skill; on timeout the whole process group is killed."""
    run_dir = run_dir_for(cfg, msg)
    query = write_query_file(run_dir, str(msg.packet["prompt_text"]))
    argv = hermes_argv(cfg, msg, query)
    loop = asyncio.get_running_loop()
    started = loop.time()
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=run_dir,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,  # never read: a pipe would only fill memory
        env=clean_env(cfg),
        start_new_session=True,
        limit=LINE_LIMIT,
    )
    assert proc.stdout is not None  # noqa: S101  # PIPE above
    events: list[dict[str, Any]] = []
    timed_out = False
    try:
        async with asyncio.timeout(timeout_s if timeout_s is not None else msg.timeout_s):
            async for line in proc.stdout:
                if (record := _parse_line(line)) is not None:
                    events.append(record)
            await proc.wait()
    except TimeoutError:
        timed_out = True
        _kill_group(proc.pid)
        await proc.wait()
    finally:
        if proc.returncode is None:  # cancelled: never leave Hermes running
            _kill_group(proc.pid)
            await proc.wait()
    duration_ms = int((loop.time() - started) * 1000)
    return build_result(
        msg,
        events,
        _final(events),
        exit_code=None if timed_out else proc.returncode,
        timed_out=timed_out,
        duration_ms=duration_ms,
    )


def _kill_group(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGKILL)


async def run_skill(msg: Run, state: "StateStore", cfg: DaemonConfig) -> None:
    """Execute and send the Result until the server acks it (then the run dir goes)."""
    state.running.add(msg.run_id)
    try:
        try:
            result = await execute(msg, cfg)
        except InvalidProfile as exc:
            result = build_result(msg, [], None, exit_code=None, timed_out=False, duration_ms=0)
            result = result.model_copy(update={"error": str(exc)})
        except (OSError, KeyError, ValueError) as exc:
            # Hermes missing or not executable, no prompt, an overlong stream-json line: a
            # failed Result now, not a silent wait for the server's run timeout. Only the
            # error kind is kept, never its message (it can quote the prompt).
            kind = type(exc).__name__
            log.warning("run_failed", extra={"run_id": str(msg.run_id), "kind": kind})
            result = build_result(msg, [], None, exit_code=None, timed_out=False, duration_ms=0)
            result = result.model_copy(update={"error": f"daemon_error:{kind}"})
        await state.send_reliably(result)
    finally:
        state.running.discard(msg.run_id)


# --- health check ------------------------------------------------------------------------


async def _hermes(cfg: DaemonConfig, *args: str) -> tuple[int, str] | None:
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
        async with asyncio.timeout(HEALTH_TIMEOUT_S):
            out, _ = await proc.communicate()
    except TimeoutError:
        _kill_group(proc.pid)
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
    if not re.fullmatch(NAME_RE, msg.profile):
        return _report(msg, exists=False, reachable=False, error="invalid profile name")
    version = await _hermes(cfg, "version")
    if version is None:
        return _report(msg, exists=False, reachable=False, error="hermes did not run")
    shown = await _hermes(cfg, "profile", "show", msg.profile)
    exists = shown is not None and shown[0] == 0
    mcp: list[str] = []
    authenticated: bool | None = None
    if exists:
        listed = await _hermes(cfg, "-p", msg.profile, "mcp", "list")
        if listed is not None and listed[0] == 0:
            mcp = parse_mcp_list(listed[1])
        status = await _hermes(cfg, "-p", msg.profile, "status")
        authenticated = None if status is None else status[0] == 0
    return _report(
        msg,
        exists=exists,
        reachable=True,
        authenticated=authenticated,
        version=parse_version(version[1]) if version[0] == 0 else None,
        mcp=mcp,
        error=None if exists else "profile not found",
    )


def _report(
    msg: HealthCheck,
    *,
    exists: bool,
    reachable: bool,
    authenticated: bool | None = None,
    version: str | None = None,
    mcp: list[str] | None = None,
    error: str | None = None,
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
    )
