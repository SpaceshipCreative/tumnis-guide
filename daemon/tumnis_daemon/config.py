"""The daemon's configuration (`/etc/tumnis/daemon.toml`, P1-04).

```toml
server_url = "wss://tumnis.example.org"   # /ws/runner is appended
runner_name = "homelab-hermes"
token_file = "/etc/tumnis/runner.token"    # the device token, mode 0600; never in the env
state_dir = "/var/lib/tumnis-daemon"
hermes_bin = "hermes"
profiles = ["tumnis-master", "acme-site"]
max_concurrent_runs = 2
# P1-06: provisioning project profiles
template_dir = "/opt/tumnis-daemon/share/profiles/project-template"
hermes_profiles_dir = "/home/tumnis-agent/.hermes/profiles"
profile_env_file = "/etc/tumnis/profile.env"   # copied to each new profile's .env, 0600
```
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TEMPLATE_DIR = Path("/opt/tumnis-daemon/share/profiles/project-template")
PROFILE_ENV_FILE = Path("/etc/tumnis/profile.env")


@dataclass(frozen=True)
class DaemonConfig:
    server_url: str
    runner_name: str
    token_file: Path
    state_dir: Path
    hermes_bin: str = "hermes"
    profiles: tuple[str, ...] = field(default_factory=tuple)
    max_concurrent_runs: int = 2  # plan default
    template_dir: Path = TEMPLATE_DIR  # the project template of this release (P1-06)
    hermes_profiles_dir: Path = field(default_factory=lambda: Path.home() / ".hermes" / "profiles")
    profile_env_file: Path | None = PROFILE_ENV_FILE

    def read_token(self) -> str:
        return self.token_file.read_text(encoding="utf-8").strip()

    @property
    def bundled_template_version(self) -> str | None:
        """The shipped template's VERSION; None when no template is installed."""
        try:
            return (self.template_dir / "VERSION").read_text(encoding="utf-8").strip() or None
        except OSError:
            return None


def load_config(path: Path) -> DaemonConfig:
    data: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    return DaemonConfig(
        server_url=str(data["server_url"]).rstrip("/"),
        runner_name=str(data["runner_name"]),
        token_file=Path(data["token_file"]),
        state_dir=Path(data["state_dir"]),
        hermes_bin=str(data.get("hermes_bin", "hermes")),
        profiles=tuple(str(p) for p in data.get("profiles", ())),
        max_concurrent_runs=int(data.get("max_concurrent_runs", 2)),
        template_dir=Path(data.get("template_dir", TEMPLATE_DIR)),
        hermes_profiles_dir=Path(
            data.get("hermes_profiles_dir", Path.home() / ".hermes" / "profiles")
        ),
        profile_env_file=Path(data.get("profile_env_file", PROFILE_ENV_FILE)),
    )
