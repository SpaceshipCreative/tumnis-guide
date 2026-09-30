"""The daemon's configuration (`/etc/tumnis/daemon.toml`, P1-04).

```toml
server_url = "wss://tumnis.example.org"   # /ws/runner is appended
runner_name = "homelab-hermes"
token_file = "/etc/tumnis/runner.token"    # the device token, mode 0600; never in the env
state_dir = "/var/lib/tumnis-daemon"
hermes_bin = "hermes"
profiles = ["tumnis-master", "acme-site"]
max_concurrent_runs = 2
hermes_home = "/home/tumnis-agent/.hermes"  # default: ~/.hermes of the daemon's user
```
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DaemonConfig:
    server_url: str
    runner_name: str
    token_file: Path
    state_dir: Path
    hermes_bin: str = "hermes"
    profiles: tuple[str, ...] = field(default_factory=tuple)
    max_concurrent_runs: int = 2  # plan default
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
        hermes_home=Path(data.get("hermes_home", Path.home() / ".hermes")),
    )
