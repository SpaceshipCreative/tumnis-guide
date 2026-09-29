"""Running a skill with Hermes (P1-04, R-25). Stub: implemented after the spec tests."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tumnis_daemon.config import DaemonConfig
from tumnis_daemon.protocol import Result, Run


class InvalidProfile(ValueError):  # noqa: N818  # the plan's word
    """A profile or skill name the protocol would never send; refused before any spawn."""


def hermes_argv(cfg: DaemonConfig, msg: Run, query_file: Path) -> list[str]:
    raise NotImplementedError(f"P1-04 {cfg} {msg} {query_file}")


def write_query_file(run_dir: Path, prompt_text: str) -> Path:
    raise NotImplementedError(f"P1-04 {run_dir} {prompt_text}")


def clean_env(cfg: DaemonConfig, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    raise NotImplementedError(f"P1-04 {cfg} {environ}")


def extract_json_object(text: str) -> dict[str, Any] | None:
    raise NotImplementedError(f"P1-04 {text}")


async def execute(msg: Run, cfg: DaemonConfig, *, timeout_s: float | None = None) -> Result:
    raise NotImplementedError(f"P1-04 {msg} {cfg} {timeout_s}")


def read_recording(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    raise NotImplementedError(f"P1-04 {path}")


def build_result(
    msg: Run,
    events: list[dict[str, Any]],
    final: dict[str, Any] | None,
    *,
    exit_code: int | None,
    timed_out: bool,
    duration_ms: int,
) -> Result:
    raise NotImplementedError(f"P1-04 {msg} {events} {final} {exit_code} {timed_out} {duration_ms}")
