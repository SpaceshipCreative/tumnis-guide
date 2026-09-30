# tumnis-daemon

The runner daemon that lives on the agent server (and later a Mac). It dials out to Tumnis at `/ws/runner` with a device token, receives task packets, runs them through Hermes and posts results. The agent server never needs an inbound port.

The daemon never imports `backend/`. Its protocol models (`tumnis_daemon/protocol.py`) are its own copies of runner protocol 1; the contract test checks them against the committed JSON Schemas in `../schemas/runner/v1/`.

## Layout

| File | Holds |
| --- | --- |
| `tumnis_daemon/config.py` | `DaemonConfig` and `load_config` (`/etc/tumnis/daemon.toml`) |
| `tumnis_daemon/protocol.py` | Protocol 1 messages, `parse_server` and the message builders |
| `tumnis_daemon/runner.py` | Running a skill with Hermes (argv, query file, clean env, result) and the profile health check |
| `tumnis_daemon/health.py` | The health check's probes: a profile's MCP servers, and its GitHub and Coolify tokens' reach (P2-10) |
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
hermes_home = "/home/tumnis-agent/.hermes" # default: ~/.hermes of the daemon's user
```

Create the runner in Settings > Agents, which shows its device token once, and write the token to `token_file`.

## Behaviour

- It refuses to run as root (exit 78); it runs as the unprivileged user `tumnis-agent`, whose home also holds Hermes's `~/.hermes`.
- A `run` becomes `hermes -p <profile> chat --query-file <file> -s <skill> --format stream-json --source tool`. No shell is used, the packet's `prompt_text` goes to the query file byte for byte, and Hermes gets only `PATH`, `HOME`, `LANG` and `HERMES_*` from the environment. A run past its timeout is killed with its whole process group.
- The skill's reply must be one JSON object (bare or in one fenced block); anything else fails the run with `no_json`.
- A health check also reads the profile folder `<hermes_home>/profiles/<name>`: its MCP servers from `config.yaml` (`mcp_servers`) and `mcp.json` (`mcpServers`; `config.yaml` wins on a name clash), reported as name, transport and the command's name or the URL's host only (never arguments, env, headers or URL credentials). Tumnis compares them with the project's tool allowlist; a server outside it marks the profile degraded and opens a review item.
- It then probes what the profile's tokens reach, on the host, so a token never leaves it. It reads them from the profile's `.env`: `GITHUB_TOKEN` or `GITHUB_PERSONAL_ACCESS_TOKEN` (checked against `api.github.com`; a repo counts as reached only with push or admin permission, since any token reads public repos) and `COOLIFY_TOKEN` or `COOLIFY_API_TOKEN`, checked against the Coolify that the same `.env` names in `COOLIFY_BASE_URL`. Every profile that uses Coolify must set `COOLIFY_BASE_URL` (`https://`, or plain `http://` only to a private or loopback address): without it the check reports the Coolify token as not configured and sends nothing, and the token never goes to a URL Tumnis supplies (Tumnis's Coolify URL is only compared with it; a different origin is reported, not probed). A token that reaches another project's repo or app marks the profile degraded. The probes run side by side and stop after 20 seconds, so the report always arrives inside the server's 30-second wait; a kind cut short reports the timeout. Give each client project its own fine-grained GitHub token limited to its repos, and its own Coolify team and API token.
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
