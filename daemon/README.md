# tumnis-daemon

The runner daemon that lives on the agent server (and later a Mac). It dials out to Tumnis at `/ws/runner` with a device token, receives task packets, runs them through Hermes and posts results. The agent server never needs an inbound port.

The daemon never imports `backend/`. Its protocol models (`tumnis_daemon/protocol.py`) are its own copies of runner protocol 1; the contract test checks them against the committed JSON Schemas in `../schemas/runner/v1/`.

## Layout

| File | Holds |
| --- | --- |
| `tumnis_daemon/config.py` | `DaemonConfig` and `load_config` (`/etc/tumnis/daemon.toml`) |
| `tumnis_daemon/protocol.py` | Protocol 1 messages, `parse_server` and the message builders |
| `tumnis_daemon/runner.py` | Running a skill with Hermes (argv, query file, clean env, result) and the profile health check |
| `tumnis_daemon/provision.py` | `provision`: a project's Hermes profile, installed from the bundled template or linked (P1-06) |
| `tumnis_daemon/state.py` | `StateStore`: results and health reports kept on disk until the server acks them |
| `tumnis_daemon/main.py` | `main` (connect, register, heartbeat, receive) and the `tumnis-daemon run` CLI |
| `systemd/tumnis-daemon.service` | The systemd unit |

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
template_dir = "/opt/tumnis-daemon/share/profiles/project-template"   # P1-06
hermes_profiles_dir = "/home/tumnis-agent/.hermes/profiles"
profile_env_file = "/etc/tumnis/profile.env"   # mode 0600, owned by tumnis-agent
```

Create the runner in Settings > Agents, which shows its device token once, and write the token to `token_file`.

## Behaviour

- It refuses to run as root (exit 78); it runs as the unprivileged user `tumnis-agent`, whose home also holds Hermes's `~/.hermes`.
- A `run` becomes `hermes -p <profile> chat --query-file <file> -s <skill> --format stream-json --source tool`. No shell is used, the packet's `prompt_text` goes to the query file byte for byte, and Hermes gets only `PATH`, `HOME`, `LANG` and `HERMES_*` from the environment. A run past its timeout is killed with its whole process group.
- The skill's reply must be one JSON object (bare or in one fenced block); anything else fails the run with `no_json`.
- A `provision` (P1-06) checks the server's template version against `template_dir/VERSION` first and refuses a mismatch before Hermes runs. `create` answers `exists` when `hermes profile show <n>` finds the profile, otherwise runs `hermes profile install <template_dir> --name <n> --yes` and copies `profile_env_file` to the new profile's `.env` (mode 0600). `link` answers `linked` or `not_found`. Created and linked names are kept in `state_dir/profiles.json` and listed in every register.
- Install the repo's `profiles/project-template` of the same release at `template_dir`: the server sends the version it expects.
- Every server message is acked. Results and health reports are kept under `state_dir/unacked/` until the server acks them and are sent again after a reconnect.

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
```
