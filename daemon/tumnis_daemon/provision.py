"""The daemon's `provision` handler (P1-06, FR-2.1, FR-5.10).

The server asks for a project's Hermes profile: `create` installs the project template
this daemon ships (`cfg.template_dir`, the same release as the server) under the given
name, `link` checks that an existing profile is there. The server names the template
version it expects, and a mismatch is refused before Hermes runs, so the daemon never
installs a template the server does not know.

    create: hermes profile show <n>   (exit 0: it exists, answer `exists`)
            hermes profile install <template dir> --name <n> --yes
    link:   hermes profile show <n>   (exit 0: `linked`, otherwise `not_found`)

After a create the operator's profile env (`cfg.profile_env_file`, the TYPESAFE key and
the Tumnis MCP URL) is copied to the new profile's `.env` with mode 0600: distributions
never carry a `.env`, and the Tumnis app never writes into a profile's home. Names this
daemon created or linked are remembered in `<state_dir>/profiles.json`, so its next
register lists them.
"""

import contextlib
import json
import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from tumnis_daemon.config import DaemonConfig
from tumnis_daemon.protocol import NAME_RE, Provision, ProvisionResult, envelope
from tumnis_daemon.runner import _hermes

if TYPE_CHECKING:
    from tumnis_daemon.state import StateStore

__all__ = ["handle_provision", "provision", "remembered_profiles"]

log = logging.getLogger(__name__)

ErrorCode = Literal["template_version_mismatch", "not_found", "hermes_error", "invalid_name"]
PROFILES_FILE = "profiles.json"
# An install copies the template and its skills; the server waits 300 s (plan default).
INSTALL_TIMEOUT_S = 240.0


def _answer(
    msg: Provision,
    status: Literal["created", "exists", "linked", "failed"],
    *,
    version: str | None = None,
    error_code: ErrorCode | None = None,
    error: str | None = None,
) -> ProvisionResult:
    return ProvisionResult(
        **envelope(msg.correlation_id),
        request_id=msg.request_id,
        profile=msg.profile,
        status=status,
        distribution_version=version if status != "failed" else None,
        error_code=error_code,
        error=error,
    )


def _copy_profile_env(cfg: DaemonConfig, name: str) -> None:
    """The operator's profile env as the profile's `.env`, mode 0600 (if one is set up)."""
    source = cfg.profile_env_file
    if source is None or not source.is_file():
        return
    home = cfg.hermes_profiles_dir / name
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = home / ".env"
    tmp = target.with_suffix(".env.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(source.read_bytes())
    os.chmod(tmp, 0o600)  # the umask may have narrowed os.open's mode
    tmp.replace(target)


async def _install(msg: Provision, cfg: DaemonConfig, version: str | None) -> ProvisionResult:
    """A new profile from the template, then its `.env`."""
    installed = await _hermes(
        cfg,
        "profile",
        "install",
        str(cfg.template_dir),
        "--name",
        msg.profile,
        "--yes",
        timeout_s=INSTALL_TIMEOUT_S,
    )
    if installed is None or installed[0] != 0:
        return _answer(
            msg,
            "failed",
            error_code="hermes_error",
            error="hermes profile install failed" if installed else "hermes did not run",
        )
    try:
        _copy_profile_env(cfg, msg.profile)
    except OSError as exc:
        log.warning("profile_env_not_copied", extra={"profile": msg.profile})
        return _answer(msg, "failed", error_code="hermes_error", error=f"profile env: {exc}")
    return _answer(msg, "created", version=version)


async def handle_provision(msg: Provision, cfg: DaemonConfig) -> ProvisionResult:
    """The answer to one `provision` request (see the module docstring)."""
    if not re.fullmatch(NAME_RE, msg.profile):
        return _answer(msg, "failed", error_code="invalid_name", error="invalid profile name")
    version = cfg.bundled_template_version
    if msg.template_version != version:
        return _answer(
            msg,
            "failed",
            error_code="template_version_mismatch",
            error=f"this daemon ships template {version or 'none'}",
        )
    shown = await _hermes(cfg, "profile", "show", msg.profile)
    exists = shown is not None and shown[0] == 0
    if msg.mode == "link":
        if exists:
            return _answer(msg, "linked", version=version)
        return _answer(msg, "failed", error_code="not_found", error="no such profile")
    if exists:
        return _answer(msg, "exists", version=version)
    return await _install(msg, cfg, version)


def remembered_profiles(state_dir: Path) -> list[str]:
    """The profiles this daemon created or linked, for its register."""
    try:
        names = json.loads((state_dir / PROFILES_FILE).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []
    return [n for n in names if isinstance(n, str) and re.fullmatch(NAME_RE, n)]


def _remember(state_dir: Path, name: str) -> None:
    names = remembered_profiles(state_dir)
    if name in names:
        return
    path = state_dir / PROFILES_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted([*names, name])), encoding="utf-8")
    tmp.replace(path)


async def provision(msg: Provision, state: "StateStore", cfg: DaemonConfig) -> None:
    """Answer a `provision` reliably (kept until the server acks it)."""
    result = await handle_provision(msg, cfg)
    if result.status != "failed":
        with contextlib.suppress(OSError):
            _remember(state.state_dir, msg.profile)
    await state.send_reliably(result)
