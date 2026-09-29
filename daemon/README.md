# tumnis-daemon

The runner daemon that lives on the agent server (and later a Mac). It dials out to Tumnis at `/ws/runner` with a device token, receives task packets, runs them through Hermes, streams run events back and posts results. The agent server never needs an inbound port.

It runs as a systemd service (`systemd/tumnis-daemon.service`) under the unprivileged user `tumnis-agent`, or under launchd on macOS (`launchd/com.tumnis.daemon.plist`), with `tumnis-daemon run --config /etc/tumnis/daemon.toml`.

Status: empty uv project (P0-01). The `tumnis_daemon` package (protocol, runner, worktree, provisioning, archive) arrives with the runner work packages in phase 1 and 2.

```bash
cd daemon && uv sync
```
