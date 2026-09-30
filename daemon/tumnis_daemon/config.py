"""The daemon's configuration (`/etc/tumnis/daemon.toml`, P1-04, P2-07).

```toml
server_url = "wss://tumnis.example.org"   # /ws/runner is appended
runner_name = "homelab-hermes"
token_file = "/etc/tumnis/runner.token"    # the device token, mode 0600; never in the env
state_dir = "/var/lib/tumnis-daemon"
hermes_bin = "hermes"
profiles = ["tumnis-master", "acme-site"]
max_concurrent_runs = 2
# P2-07 (defaults shown)
agent_home = "/home/tumnis-agent"          # a `path` code location must sit under it...
paths_dropin = "/etc/systemd/system/tumnis-daemon.service.d/paths.conf"  # ...or be listed
kill_grace_s = 10.0                        # SIGTERM to SIGKILL on cancel
outbox_max_bytes = 52428800                # 50 MiB; past it, old log lines give way
hermes_home = "/home/tumnis-agent/.hermes"  # P2-10; default: ~/.hermes of the daemon's user
```
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

AGENT_HOME: Final = Path("/home/tumnis-agent")  # R-26
PATHS_DROPIN: Final = Path("/etc/systemd/system/tumnis-daemon.service.d/paths.conf")
KILL_GRACE_S: Final = 10.0  # plan default
OUTBOX_MAX_BYTES: Final = 50 * 1024 * 1024  # plan default


@dataclass(frozen=True)
class DaemonConfig:
    server_url: str
    runner_name: str
    token_file: Path
    state_dir: Path
    hermes_bin: str = "hermes"
    profiles: tuple[str, ...] = field(default_factory=tuple)
    max_concurrent_runs: int = 2  # plan default
    agent_home: Path = AGENT_HOME
    paths_dropin: Path = PATHS_DROPIN
    kill_grace_s: float = KILL_GRACE_S
    outbox_max_bytes: int = OUTBOX_MAX_BYTES
    # Hermes's home; a profile lives in <hermes_home>/profiles/<name> (P2-10's probes)
    hermes_home: Path = field(default_factory=lambda: Path.home() / ".hermes")

    def read_token(self) -> str:
        return self.token_file.read_text(encoding="utf-8").strip()


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
        agent_home=Path(data.get("agent_home", AGENT_HOME)),
        paths_dropin=Path(data.get("paths_dropin", PATHS_DROPIN)),
        kill_grace_s=float(data.get("kill_grace_s", KILL_GRACE_S)),
        outbox_max_bytes=int(data.get("outbox_max_bytes", OUTBOX_MAX_BYTES)),
        hermes_home=Path(data.get("hermes_home", Path.home() / ".hermes")),
    )
