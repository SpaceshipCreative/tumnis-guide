# tumnis-daemon

The runner daemon that lives on the agent server (and later a Mac). It dials out to Tumnis at `/ws/runner` with a device token, receives task packets, runs them through Hermes and posts results. The agent server never needs an inbound port.

The daemon never imports `backend/`. Its protocol models (`tumnis_daemon/protocol.py`) are its own copies of runner protocols 1 and 2; the contract test checks them against the committed JSON Schemas in `../schemas/runner/v1/` and `../schemas/runner/v2/`.

## Layout

| File | Holds |
| --- | --- |
| `tumnis_daemon/config.py` | `DaemonConfig` and `load_config` (`/etc/tumnis/daemon.toml`) |
| `tumnis_daemon/protocol.py` | Protocol 1 and 2 messages, `parse_server` and the message builders |
| `tumnis_daemon/runner.py` | Running a skill with Hermes (argv, query file, clean env, streaming, cancel, result) and the profile health check |
| `tumnis_daemon/outbox.py` | The SQLite outbox (`state_dir/state.db`) and the seen-set of server commands already taken |
| `tumnis_daemon/state.py` | `StateStore`: what goes to the outbox, what is replayed after a reconnect, per-run cancel switches |
| `tumnis_daemon/worktree.py` | Per-run git worktrees: prepare, remove, and clean up leftovers at start-up |
| `tumnis_daemon/main.py` | `main` (connect, register, replay, heartbeat, receive) and the `tumnis-daemon run` CLI |
| `systemd/tumnis-daemon.service` | The hardened systemd unit (agent server) |
| `launchd/com.tumnis.daemon.plist` | A per-user LaunchAgent (Mac) |

## Configuration

```toml
# /etc/tumnis/daemon.toml
server_url = "wss://tumnis.example.org"   # /ws/runner is appended
runner_name = "homelab-hermes"
token_file = "/etc/tumnis/runner.token"    # the device token, mode 0600; never in the env
state_dir = "/var/lib/tumnis-daemon"
hermes_bin = "hermes"
profiles = ["tumnis-master", "acme-site"]
max_concurrent_runs = 2
# Optional (defaults shown)
agent_home = "/home/tumnis-agent"          # a `path` code location must sit under it...
paths_dropin = "/etc/systemd/system/tumnis-daemon.service.d/paths.conf"  # ...or be listed here
kill_grace_s = 10.0                        # SIGTERM to SIGKILL on cancel
outbox_max_bytes = 52428800                # 50 MiB; past it, the oldest log lines give way
```

Create the runner in Settings > Agents, which shows its device token once, and write the token to `token_file`.

## Behaviour

- It refuses to run as root (exit 78); it runs as the unprivileged user `tumnis-agent`, whose home also holds Hermes's `~/.hermes`.
- It registers offering protocols 1 and 2. The server picks 2 only when it also sees the protocol-2 capabilities (`stream`, `cancel`, `upload_artifact`); otherwise the session stays on 1 and behaves as P1-04's daemon did.
- A `run` becomes `hermes -p <profile> chat --query-file <file> -s <skill> --format stream-json --source tool`. No shell is used, the packet's `prompt_text` goes to the query file byte for byte, and Hermes gets only `PATH`, `HOME`, `LANG` and `HERMES_*` from the environment. A run past its timeout is killed with its whole process group.
- The skill's reply must be one JSON object (bare or in one fenced block); anything else fails the run with `no_json`.
- On protocol 2, each stream-json record goes to the server as it arrives (`stream`: log, tool call or file), along with `status` messages (started with the profile version, cancelling, gap) and the files the run touched (`git status --porcelain -z`).
- A `cancel` sends Hermes's process group SIGTERM, then SIGKILL after `kill_grace_s`, and the result is `cancelled`. On a protocol-1 session the server never sends `cancel`.
- A run with `workdir_policy: worktree` works in a linked git worktree under `state_dir/worktrees/<run id>`, removed when the run ends whatever the outcome; leftovers are removed at start-up. A `path` location must resolve under `agent_home` or a `ReadWritePaths` entry of `paths_dropin`, or the run is refused. A `repo` location is cloned once as a mirror under `state_dir/repos/`.
- Every outbound message goes to the outbox before it is sent and stays there until the server acks it; after a reconnect the rest is replayed in order with the same `message_id`, and the server stores each id once. Server commands already taken are remembered, so a resent `run` never starts Hermes twice. Past `outbox_max_bytes` the oldest log lines are dropped and declared by one `status` gap per run; results, tool calls, files and artifacts are never dropped.
- Every server message is acked: `ack{ack_of}` on protocol 1, `ack{message_ids}` on protocol 2.

## Run

```bash
cd daemon && uv sync
uv run tumnis-daemon run --config /etc/tumnis/daemon.toml
uv run pytest && uv run ruff check . && uv run mypy
```

On the agent server, install to `/opt/tumnis-daemon` and:

```bash
sudo cp systemd/tumnis-daemon.service /etc/systemd/system/
sudo systemctl enable --now tumnis-daemon && journalctl -u tumnis-daemon -f
systemd-analyze security tumnis-daemon.service
```

A project whose code sits outside `/home/tumnis-agent` needs its path in the unit's drop-in, `/etc/systemd/system/tumnis-daemon.service.d/paths.conf`:

```ini
[Service]
ReadWritePaths=/srv/projects/acme-site
```

On a Mac, see the install steps at the top of `launchd/com.tumnis.daemon.plist`.
