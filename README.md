# Tumnis Guide

A daily task organizer for freelancers and solopreneurs. Every project becomes a plan that you and a team of AI agents (Hermes) carry out together: you see the next step, the agents do the parts they can, and you answer their questions and approve their work. It runs on your own server, on your private network, and you reach it from a browser or your phone.

Under the hood it is a modular Python monolith (FastAPI, DBOS, Postgres 18) with a React PWA, and a small runner daemon on the machine where your agents run. Agents and scripts use one surface: REST under `/v1` and MCP tools at `/mcp`.

- Product: [docs/PRD.md](docs/PRD.md)
- Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), decisions in [docs/adr/](docs/adr/)
- Running it day to day: [docs/OPERATIONS.md](docs/OPERATIONS.md)
- What changed: [CHANGELOG.md](CHANGELOG.md)
- Rules for coding agents: [AGENTS.md](AGENTS.md)

## Requirements

- A Linux server or VM (the README job uses Ubuntu 24.04) with `sudo` rights.
- Docker Engine with the Docker Compose plugin 2.24.0 or later, `git`, `openssl`, `curl` and `python3`.
- [Tailscale](https://tailscale.com/) (or another VPN) to reach it from your phone. Tumnis never needs a port open to the internet, and you should not open one.
- For agents: a second machine (or the same one) with [Hermes](https://hermes-agent.nousresearch.com/docs/) and the runner daemon ([Agents](#agents)).

## Install

These steps install Tumnis on one server with Docker Compose, without any other tooling. The automated install and first-run commands below are tested: the README job (`.github/workflows/readme.yml`) runs them, in order, on a fresh Ubuntu 24.04 VM for every change to this file or to `deploy/`, every night and on every release.

### 1. Check the tools

```bash readme:install:10
docker --version
docker compose version
openssl version
```

### 2. Get the code

```bash readme:manual
git clone https://github.com/SpaceshipCreative/tumnis-guide.git
cd tumnis-guide
```

Run every command from here on in that folder.

### 3. Choose the address

Set these four values in your shell. `TUMNIS_HOST` is the name you will open in the browser: `localhost` to try Tumnis on the server itself, or the server's Tailscale name (for example `tumnis.tailnet-name.ts.net`) to use it from your phone. `TUMNIS_EMAIL` is your sign-in address, and `TUMNIS_TIMEZONE` your IANA time zone (for example `Europe/Berlin`). `TUMNIS_CA` is what the checks below trust: the self-signed certificate made in step 6, or, with a Tailscale certificate, the system's own store (step 6 sets it).

```bash readme:env
TUMNIS_HOST=localhost
TUMNIS_CA=/etc/tumnis/https/tumnis.crt
TUMNIS_EMAIL=you@example.com
TUMNIS_TIMEZONE=UTC
```

### 4. Create the keys

Three secret files live in `/etc/tumnis/secrets`, readable only by the service user (uid 10001): the master key, which protects every secret Tumnis stores; the API key pepper, which protects sessions, API keys and runner and task tokens; and the token that guards `/metrics`. An existing file is never overwritten.

```bash readme:install:20
sudo install -d -m 0711 /etc/tumnis/secrets
for name in tumnis_master_key tumnis_pepper tumnis_metrics_token; do
  file="/etc/tumnis/secrets/$name"
  sudo test -e "$file" && continue
  if [ "$name" = tumnis_metrics_token ]; then
    secret="$(openssl rand -hex 32)"
  else
    secret="{\"active\": 1, \"keys\": {\"1\": \"$(openssl rand -base64 32)\"}}"
  fi
  printf '%s\n' "$secret" | sudo sh -c "umask 077 && cat > '$file'"
  sudo chown 10001:10001 "$file"
  sudo chmod 0400 "$file"
done
sudo ls -l /etc/tumnis/secrets
```

Copy the master key and the pepper to a password manager now. Without the master key, every secret Tumnis stores (and each sign-in's TOTP secret) is unreadable; without the pepper, every API key, runner and task token, and session stops working ([docs/OPERATIONS.md](docs/OPERATIONS.md)).

### 5. Write the settings

`.env` in the repository root holds the deployment's settings: the database passwords (generated here), the host name and the backups switch. [.env.example](.env.example) explains every variable.

```bash readme:install:30
[ -e .env ] || cp .env.example .env
chmod 600 .env
for name in APP_DB_PASSWORD OWNER_DB_PASSWORD POSTGRES_PASSWORD; do
  grep -q "^$name=." .env && continue
  value="$(openssl rand -hex 24)"
  if grep -q "^$name=" .env; then
    sed -i "s/^$name=.*/$name=$value/" .env
  else
    printf '%s=%s\n' "$name" "$value" >> .env
  fi
done
sed -i -e "s|^TUMNIS_HOST=.*|TUMNIS_HOST=$TUMNIS_HOST|" \
  -e "s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=https://$TUMNIS_HOST|" .env
grep -E '^(COMPOSE_FILE|COMPOSE_PROFILES|TUMNIS_HOST|PUBLIC_BASE_URL|TUMNIS_BIND_ADDRESS)=' .env
```

Tumnis starts with backups off (`TUMNIS_BACKUPS=off`). Turn them on once the install works: [docs/OPERATIONS.md, Backups](docs/OPERATIONS.md#backups).

### 6. Get an HTTPS certificate

Tumnis is served over HTTPS only, by a small proxy (Caddy) that uses the certificate in `/etc/tumnis/https`.

**For real use, with Tailscale.** Install Tailscale on the server and on your phone, turn on HTTPS certificates for your tailnet (admin console, DNS, HTTPS Certificates), and set `TUMNIS_HOST` above to the server's Tailscale name. Then bind the proxy to the server's Tailscale address, so only devices on your tailnet can reach it, and fetch the certificate:

```bash readme:manual
sudo install -d -m 0755 /etc/tumnis/https
address="$(tailscale ip -4)"
if grep -q '^TUMNIS_BIND_ADDRESS=' .env; then
  sed -i "s|^TUMNIS_BIND_ADDRESS=.*|TUMNIS_BIND_ADDRESS=$address|" .env
else
  printf 'TUMNIS_BIND_ADDRESS=%s\n' "$address" >> .env
fi
sudo tailscale cert --cert-file /etc/tumnis/https/tumnis.crt \
  --key-file /etc/tumnis/https/tumnis.key "$TUMNIS_HOST"
TUMNIS_CA=/etc/ssl/certs/ca-certificates.crt  # a Let's Encrypt certificate: trust the system's roots
```

The certificate lasts 90 days; [docs/OPERATIONS.md, Certificates](docs/OPERATIONS.md#certificates) has the monthly renewal.

**To try it on the server itself.** Without Tailscale, make a self-signed certificate. Browsers warn about it, and phones do not trust it, so use it with `TUMNIS_HOST=localhost` only. An existing certificate is kept.

```bash readme:install:40
sudo install -d -m 0755 /etc/tumnis/https
if ! sudo test -e /etc/tumnis/https/tumnis.crt; then
  sudo openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 825 \
    -subj "/CN=$TUMNIS_HOST" -addext "subjectAltName=DNS:$TUMNIS_HOST" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -keyout /etc/tumnis/https/tumnis.key -out /etc/tumnis/https/tumnis.crt
  sudo chmod 0600 /etc/tumnis/https/tumnis.key
  sudo chmod 0644 /etc/tumnis/https/tumnis.crt
fi
openssl x509 -in /etc/tumnis/https/tumnis.crt -noout -subject -enddate
```

### 7. Build and start

Build the Tumnis image from this checkout (the first build takes a while), then start the stack. Database migrations run first. `--wait` returns once the services with a health check are healthy (the api's is its own `/health/ready`) and the others are running; step 8 then checks readiness through the proxy.

```bash readme:install:50 timeout=2400
docker build -f deploy/Dockerfile --build-arg VERSION=local \
  -t ghcr.io/spaceshipcreative/tumnis:local .
```

```bash readme:install:60 timeout=1800
docker compose up -d --build --wait --wait-timeout 1500
docker compose ps
```

### 8. Check that it answers

```bash readme:install:70 timeout=600 expect="status":\s*"(ok|degraded)"
body=""
for attempt in $(seq 1 60); do
  body="$(curl -fsS --cacert "$TUMNIS_CA" "https://$TUMNIS_HOST/health/ready")" && break
  sleep 5
done
echo "$body"
curl -fsS --cacert "$TUMNIS_CA" "https://$TUMNIS_HOST/" | grep -q 'id="root"'
echo "Tumnis answers at https://$TUMNIS_HOST"
```

`/health/ready` answers `ok`, or `degraded` while backups are still off. Anything else: `docker compose logs api worker` says why.

### With Coolify instead

On a server managed by [Coolify](https://coolify.io/), Tumnis deploys from `main` after CI is green (`.github/workflows/deploy.yml` on a self-hosted runner). Create the three secret files as in step 4, create a Docker Compose resource from this repository with `deploy/compose.yaml`, and set its environment in Coolify ([deploy/.env.example](deploy/.env.example) lists every variable; the optional hosted provider keys go in `deploy/hosted-keys.env` instead, as in [Agents](#agents)):

```bash readme:manual
APP_DB_PASSWORD=<openssl rand -hex 24>
OWNER_DB_PASSWORD=<openssl rand -hex 24>
POSTGRES_PASSWORD=<openssl rand -hex 24>
TUMNIS_VERSION=<a release tag, for example v1.0.0>
PUBLIC_BASE_URL=https://tumnis.example
```

Coolify's own proxy serves HTTPS, so leave `COMPOSE_PROFILES` unset (no Caddy). Keep Coolify and Tumnis on your VPN or tailnet only.

## First run

Open `https://<TUMNIS_HOST>/setup` in a browser on your tailnet (for a `localhost` install, on the server itself). Enter your email, a password of at least 12 characters and your time zone, then scan the QR code with an authenticator app (any TOTP app) and type its 6-digit code. You are signed in, and the setup page closes for good: Tumnis has one owner per install.

Without a browser, the same steps from the server's shell. The script prints the `otpauth://` link to add to your authenticator app, then signs in again with a fresh code to prove the account works. Export `TUMNIS_PASSWORD` first (`export TUMNIS_PASSWORD=...`) to choose the password, since the script reads it from the environment; otherwise it makes one and prints it once.

```bash readme:first-run:10 timeout=300 expect=^Signed\sin\sas\s
python3 - "$TUMNIS_HOST" "$TUMNIS_CA" "$TUMNIS_EMAIL" "$TUMNIS_TIMEZONE" <<'PY'
import base64, hashlib, hmac, json, os, secrets, ssl, struct, sys, time
import urllib.parse, urllib.request

host, ca_file, email, timezone = sys.argv[1:5]
base = f"https://{host}"
tls = ssl.create_default_context(cafile=ca_file)
password = os.environ.get("TUMNIS_PASSWORD") or secrets.token_urlsafe(18)


def call(method, path, body=None, cookie=None):
    request = urllib.request.Request(base + path, method=method)
    request.add_header("Content-Type", "application/json")
    if cookie:
        request.add_header("Cookie", cookie)
    data = None if body is None else json.dumps(body).encode()
    with urllib.request.urlopen(request, data, context=tls, timeout=30) as answer:
        return json.loads(answer.read() or b"null"), answer.headers.get_all("Set-Cookie") or []


def code(secret, step):  # RFC 6238: SHA-1, 6 digits, 30-second steps
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    return f"{(struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


setup, _ = call("POST", "/v1/setup", {"email": email, "password": password, "timezone": timezone})
uri = setup["otpauth_uri"]
secret = urllib.parse.parse_qs(urllib.parse.urlparse(uri).query)["secret"][0]
print(f"Add this to your authenticator app: {uri}")
if not os.environ.get("TUMNIS_PASSWORD"):
    print(f"Your password (shown once): {password}")
call("POST", "/v1/setup/totp", {"setup_token": setup["setup_token"], "code": code(secret, int(time.time()) // 30)})

time.sleep(31 - time.time() % 30)  # each code works once: wait for the next one
login, _ = call("POST", "/v1/auth/login", {"email": email, "password": password})
_, cookies = call("POST", "/v1/auth/totp", {"preauth": login["preauth"], "code": code(secret, int(time.time()) // 30)})
session = "; ".join(c.split(";", 1)[0] for c in cookies)
account, _ = call("GET", "/v1/auth/account", cookie=session)
print(f"Signed in as {account['email']}")
PY
```

Then, in the app:

1. **Settings > Workspace** sets the workspace's time zone; **Settings > Working hours** sets the hours your plan fits into.
2. Create a project, write its brief, and add its tasks.
3. Keep the `otpauth://` link (or a second authenticator) somewhere safe: a lost phone otherwise needs the server's shell to recover (`tumnis admin reset-totp`, [docs/OPERATIONS.md](docs/OPERATIONS.md#account-recovery-and-maintenance-commands)).

## Your day

- **Capture.** Press `/` (or **Quick add** on the phone) anywhere and type the task; it works offline too. Tumnis labels it AI, Human or Hybrid with a one-line reason you can override in one click, and adds a first action and an estimate. Today's captures show under **Just added** on the dashboard, and a task's drawer has its **History**: every change, who made it and when.
- **Daily plan.** At 08:30 on weekdays Tumnis proposes the day's plan from your tasks; accept it, swap a task or remove one. From 16:00, **Close the day** wraps it up.
- **Focus.** The focus bar shows the one next step. Pick how hard Tumnis keeps you on it: quiet, nudge, coach or guardrail (guardrail notices detours and offers the way back). Press **Stuck** and the project's agent works on a first step: it splits off a small subtask or takes the step itself, and if nothing comes back in time you get the task's first action with a 10-minute timer.
- **Review.** `/review` is the queue of what waits for you, such as agent results to accept. Accepting the result of a run started from **Stuck** marks that step done and keeps the task in progress; rejecting it reopens the step with your reason.
- **Inbox and Activity.** Per project, the Inbox shows what arrived and Activity what happened; **Ask the agent** sends a question to the project's agent.
- **Search.** The Search page (`/search`), or Ctrl+K (Cmd+K on a Mac) on any screen, finds tasks and projects.

## Connect sources

Everything a project knows lives in its knowledge base: documents, notes and links, searched by you and quoted to agents. Files go through a virus scan (ClamAV) before anyone reads them. Text extraction (Docling) is not in this release's image yet: on an install, an uploaded or synced file is scanned and then marked failed (`extraction_failed`), so its text is not searchable or quoted to agents ([Not yet available](#not-yet-available)). Notes written in the editor are indexed without Docling and stay searchable.

- **Upload** files in a project's Knowledge section (or drop one on the project's composer), up to 50 MiB each: PDF, Word, Excel, PowerPoint, text, Markdown, CSV, HTML and images (PNG, JPEG, TIFF, WebP).
- **Notes and links** written in the editor, or saved from a URL.
- **Where files live** (Settings > Storage): a folder on the server, a mounted share (SMB or NFS), an S3 bucket or an SFTP server. An SFTP server's host key is trusted only after you compare its fingerprint. Each project gets a folder on the default location, synced both ways: a file you drop in the folder appears in the knowledge base, and an upload appears in the folder.
- **Calendar** (Settings > Calendar): connect a Google account with your own OAuth client (read-only); busy time shapes the daily plan.
- **GitHub and Coolify** for project agents: set through the API (`PUT /v1/settings/github` with a token, the allowed repositories and a webhook secret; `PUT /v1/settings/coolify` with the base URL and a token). Pull requests are tracked on their tasks.
- **A folder you already keep** (a project's **Folder** section, "Use a folder you already keep"): point the project at an existing folder on any storage location. Tumnis then writes only inside a `Tumnis/` subfolder there and never renames, moves, overwrites or deletes your own files. **Move the folder** copies every file to a new place and checks each one before the project switches; the old copy stays until you choose to remove it.

## Agents

Agents run on a machine of your own (the "agent server"), in [Hermes](https://hermes-agent.nousresearch.com/docs/) profiles: one master profile for your whole workspace and one profile per client project, so each project's agent sees only that project's repositories, credentials and knowledge. The runner daemon connects out to Tumnis, so the agent server needs no open port.

1. **Create the runner** in Settings > Agents. Its device token is shown once.
2. **Install the daemon** on the agent server and write the token to its token file: [daemon/README.md](daemon/README.md) (`/etc/tumnis/daemon.toml`, the `tumnis-daemon` systemd unit, run as the `tumnis-agent` user).
3. **Install the profiles** from [profiles/](profiles/README.md): `master` and `project-template` (a new project gets a profile made from the template, or links one you already have). Each installed profile has its own `.env`, copied from its `.env.example`:
   - `TUMNIS_URL` and `TUMNIS_TOKEN`: this install and an API key for the profile (Settings > API keys, with only the scopes it needs). The master's key needs the `delegate` scope.
   - `TYPESAFE_API_KEY`: the Jev (TypeSafe) MCP server's key.
   - Project profiles: `GITHUB_PERSONAL_ACCESS_TOKEN` (a fine-grained token limited to that project's repositories), and `COOLIFY_BASE_URL` with `COOLIFY_TOKEN` from that project's own Coolify team.
   - Master profile, for Discord: `DISCORD_BOT_TOKEN`, `DISCORD_ALLOWED_USERS` (your Discord user id) and `DISCORD_ALLOWED_CHANNELS` with `DISCORD_HOME_CHANNEL` (one channel id). Focus messages and urgent notifications then reach you there, and you can answer the master agent from Discord.
4. **Run a task.** On a task, **Run** hands it to the project's agent: a run streams its log, may ask you a question or ask for approval, and ends with a result for you to accept. The master agent can delegate work to project agents. The kill switch in the header stops every agent at once; a project can be paused on its own.

**Unattended runs.** Settings > Unattended runs sets the nights and the hours when queued AI tasks run while you sleep, in the workspace's time zone (an end before the start runs overnight); a project can set its own window in its Schedule section. On an AI task, **Run unattended** queues it for the next window; a task that took in outside content never runs unattended. **Close the day** lists what is queued overnight, and the night's results, and each task that could not run with its reason, go to `/review`; their notifications are held until 15 minutes before your first working hour.

The profile health check (every 15 minutes, and on demand in Settings > Agents) marks a profile degraded when a token reaches another project's repository or app, or when it has an MCP server outside the project's tool allowlist.

**Any MCP client.** Agents reach Tumnis through MCP tools at `/mcp` (Streamable HTTP, stateless) and a REST twin for every tool under `/v1` with the same schema. The tool list, each tool's scope and its REST twin are in [schemas/mcp/v1/tools.json](schemas/mcp/v1/tools.json). Give each client its own API key with only the scopes it needs. For Claude Code:

```bash
claude mcp add --transport http tumnis https://tumnis.example/mcp \
  --header "Authorization: Bearer $TUMNIS_API_KEY"
```

A client that only speaks stdio runs `tumnis mcp-stdio`, which forwards each message to `$TUMNIS_URL/mcp` with `TUMNIS_API_KEY` as the bearer (it sends the key over plain `http://` only to `localhost` or a LAN address). Use an `https://` URL for anything but `localhost`: over plain `http://`, anyone who can watch your network can read and reuse the key. For Claude Code:

```bash
claude mcp add --env TUMNIS_API_KEY=<your key> --env TUMNIS_URL=https://tumnis.example --transport stdio \
  tumnis -- uv run --directory /path/to/tumnis-guide/backend tumnis mcp-stdio
```

**Optional local models.** A vLLM server on your network can write placeholder first actions and spoken focus messages (`GENERATION__BASE_URL`, `GENERATION__MODEL`), embed knowledge for hybrid search (`EMBEDDINGS__BASE_URL`, `EMBEDDINGS__MODEL`, `EMBEDDINGS__DIMS`) and read hard scanned pages (`KNOWLEDGE__VISION_BASE_URL`, `KNOWLEDGE__VISION_MODEL`). Without them Tumnis works the same, with full-text search. Set them on the api and worker services through a compose override file of your own, `deploy/compose.local.yaml`, and `COMPOSE_FILE=deploy/compose.yaml:deploy/compose.local.yaml` in `.env`.

**Optional hosted providers.** Instead of your own servers, an OpenAI-compatible hosted API can speak focus messages (`SPEECH__HOSTED_BASE_URL`, `SPEECH__HOSTED_MODEL`, optionally `SPEECH__HOSTED_VOICE`, and `SPEECH__HOSTED_API_KEY`) or embed knowledge (`EMBEDDINGS__HOSTED_BASE_URL`, `EMBEDDINGS__HOSTED_MODEL`, `EMBEDDINGS__HOSTED_DIMS` (1024 when blank, at most 2000) and `EMBEDDINGS__HOSTED_API_KEY`). Set them in `deploy/hosted-keys.env`, not in `.env`: copy [deploy/hosted-keys.env.example](deploy/hosted-keys.env.example) to `deploy/hosted-keys.env` (mode 600; git ignores it) and uncomment only the lines you use. Only the worker reads that file. Each provider is off until its URL, model and key are all set, a base URL must be `https://` when its key is set (otherwise the worker refuses to start: `hosted_url_requires_https`), and the keys never go into the database. A local-only project's text is never sent to a hosted provider, and a local embedder stays preferred when both are set.

## On the phone

Tumnis is a PWA: open `https://<TUMNIS_HOST>` on your phone over Tailscale and add it to the Home Screen.

- **Notifications.** Settings > Account > Enable push. On iPhone and iPad, add Tumnis to the Home Screen first (iOS 16.4 or later) and open it from there.
- **Voice.** Settings > Voice turns on spoken focus messages per focus level (off until a level is ticked); the words are exactly the message in the focus bar. By default the phone speaks with its own voice, so the text stays on the device. iOS Safari speaks only after a tap: ticking a level in Settings > Voice is that tap, so turn voice on from the phone itself. For better voices, run Piper's HTTP server on the agent server (`python3 -m piper.http_server -m <voice>`, port 5000), set `SPEECH__PIPER_URL` (and optionally `SPEECH__PIPER_VOICE`) on the worker, and choose "The server's voice"; when Piper is down the device speaks the same text instead.

## Operate

[docs/OPERATIONS.md](docs/OPERATIONS.md) covers health and alerts, turning on backups (pgBackRest to a local volume and Backblaze B2), the quarterly restore drill, upgrades and rollback, rotating the master key, certificates and account recovery. In short:

- `/health/live` and `/health/ready` for monitors; `/metrics` for Prometheus, behind the token in `/etc/tumnis/secrets/tumnis_metrics_token`.
- Upgrade: check out the release tag, rebuild the image and `docker compose up -d --wait`; migrations run first. Roll back by redeploying the previous image; the database never rolls back.
- Logs: `docker compose logs -f api worker`.

## Not yet available

These are planned but not in this release:

- **Text extraction from files.** Docling, which reads the text and tables out of uploaded and synced files, is not installed in the v1 image, so on an install every file ends `extraction_failed` after its virus scan. Notes and links are not affected.
- **Inbox Zero, Granola and chat connectors**, matching what they bring in to your tasks, and proposal runs on it; **Google Docs** as a source. They wait for recorded tests against the real services. Discord is the chosen first chat provider (an ADR, 0014, will record it); today Discord already reaches the master agent through its Hermes profile ([Agents](#agents)). **Settings > Connections**, where these accounts will be connected, synced and signed in again, is in place, but it has no provider to connect yet. Its rules already hold: the sign-in page must use https (otherwise Tumnis says "The sign-in page is not secure (it must use https), so it was not opened."), the other OAuth endpoints must use https or plain http to a LAN address, a connection whose sync stopped half way recovers on the next sync cycle, and a settings save that crosses someone else's edit is refused (409) and the form reloads their version to edit again.
- **More sources:** Obsidian vaults and S3 buckets as knowledge sources (beyond S3 as a storage location). Coming in a later update.
- **Retention rules** for old data. Coming in a later update.
- **Settings screens for GitHub and Coolify, and knowledge in search results.** Today GitHub and Coolify are set through the API ([Connect sources](#connect-sources)), and agents and the API search knowledge (`GET /v1/knowledge/search`).
- **Hosted mode** (Tumnis run for several customers) is v2.

## Develop

Prerequisites: [uv](https://docs.astral.sh/uv/) (Python 3.13), Node.js 22 or later with npm, [pre-commit](https://pre-commit.com/), and Docker for integration tests.

```bash
cd backend && uv sync          # Python toolchain and dependencies
cd ../frontend && npm ci       # ESLint, Prettier, TypeScript
cd .. && pre-commit install    # run the hooks on every commit
make check                     # lint, typecheck and unit tests; run before every commit
```

| Command | Does |
| --- | --- |
| `make test` | Unit and contract tests |
| `make test-int` | Integration tests against Postgres and containers |
| `make e2e` | Playwright journeys against the seeded app with fakes |
| `make gen` | Regenerate OpenAPI, JSON Schemas and the TypeScript client |
| `make seed` | Load the seed set |

The local stack with fakes and the seed set (the one CI's end-to-end job and Playwright use):

```bash
make up     # build and start deploy/compose.test.yaml; api on 127.0.0.1:8080 (TUMNIS_TEST_PORT moves it)
make down   # stop it and delete its volumes
```

The tests that run the real Docling pipeline (the `docling` marker: A1.5's integration tests and the extraction set) are skipped unless the optional `docling` dependency group is installed: `cd backend && uv sync --group docling`, then `uv run --group docling pytest -m docling`. It takes torch's CPU wheels from `https://download.pytorch.org/whl/cpu` and, on the first run, about 570 MB of models from Hugging Face and ModelScope. CI runs them in their own workflow (`.github/workflows/docling.yml`), which is not a required check. The group needs typer below 0.27, so Renovate holds typer there.

Every process refuses to start (exit 78) on a configuration that would put a preview near production: `DEPLOYMENT_ENV=preview` needs `TUMNIS_ADAPTERS=fake` and no Jev key, and every process checks the database's `deployment_marker` against `DEPLOYMENT_ENV`.

The README's own commands are run by `scripts/readme_test.py` ([scripts/README.md](scripts/README.md)): `python3 scripts/readme_test.py --list` shows the tagged blocks. A shell block in the Install section must carry a `readme:` tag, or the unit tests fail.

Work follows the TDD rules in [AGENTS.md](AGENTS.md): spec tests land red in a `wp/<WP>-spec` PR, then code turns them green in `wp/<WP>-impl`.
