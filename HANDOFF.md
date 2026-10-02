# P4-06 handoff (continuation 0 → 1)

Work package: P4-06 "README, docs and the v1 release". The binding prompt is `~/tumnis-coordinator/prompts/wave1/P4-06.txt`; read it in full. Branch `wp/P4-06`, pushed with `/usr/bin/git push origin HEAD:wp/P4-06`. Scratch dir `/tmp/claude-1002/P4-06-c0/`. NEVER merge, tag or publish a release.

## State

- **PR: not opened yet.** The coordinator (binding): CI runs only on PRs. Open a **DRAFT** PR straight away (`gh pr create --draft --base main --head wp/P4-06 --title "[P4-06] impl: README, docs and the v1 release tooling" --body-file /tmp/claude-1002/P4-06-c0/pr-body.md`) so CI gives run ids for marker removals (decision 78). Request CodeRabbit (`gh pr comment <url> --body "@coderabbitai review"`) ONCE, when you mark it ready.
- Base: origin/main 21be4591. main has since moved (6ec80f33, P2-06 #139 merged). Merge origin/main in before opening the PR; then P2-06 needs no TODO(coordinator) marker.
- `make check` passed on 79ba52fd. Needs `npm ci --prefix frontend` first, plus `SEMGREP_SETTINGS_FILE`, `SEMGREP_LOG_FILE` and `SEMGREP_VERSION_CACHE_PATH` exported to files under /tmp/claude-1002/P4-06-c0/ (~/.semgrep is read-only).

Commits on wp/P4-06:

| SHA | What |
| --- | --- |
| b985f12f | test(meta): P4-06 spec tests (red): test_readme_test.py (T-01..04), test_release_checklist.py (T-06), test_release_assets.py (T-07), test_docs.py (T-05, T-08), fixture backend/tests/meta/data/readme_fixture.md |
| 1c92ab77 | scripts/readme_test.py harness; T-01..04 markers removed. **No CI run id yet: cite one once CI runs.** The T-02 First run assertion in my own (unmerged) test was corrected to [44] in that commit. |
| 22073fdb | scripts/release/verify_assets.py, plus release.yml schemas tarball and `verify-assets` step; T-07 marker removed (no CI id yet). |
| 79ba52fd | deploy/compose.yaml proxy (Caddy) + TUMNIS_BACKUPS + PUBLIC_BASE_URL, deploy/caddy/Caddyfile, .env.example, .github/workflows/readme.yml |

Markers still on: T-P4-06-05 (README), T-P4-06-06 (release checklist), T-P4-06-08 (OPERATIONS).

## Uncommitted draft

**`/tmp/claude-1002/P4-06-c0/OPERATIONS.md`** is a complete draft of `docs/OPERATIONS.md`. Copy it to `docs/OPERATIONS.md`.

With it in place, T-P4-06-08 XPASSes (strict) locally, which is why it isn't committed: make check would fail. The order is:
1. Push it inside the draft PR.
2. CI shows XPASS(strict).
3. Remove the marker citing that run id.

The draft already reflects the fact sheet below. Check that it still needs no TODO(coordinator) for anything on main.

## Remaining steps

1. Copy OPERATIONS.md in (see above).
2. Write **README.md** (rewrite; keep the Develop section's existing content).
   - Install section: every shell block tagged; T-05 requires Install coverage == [] and a non-empty install suite. Planned blocks:
     - `install:10`: docker, compose and openssl versions.
     - `readme:manual`: git clone.
     - `readme:env`: TUMNIS_HOST=localhost, TUMNIS_CA=/etc/tumnis/https/tumnis.crt, TUMNIS_EMAIL=you@example.com, TUMNIS_TIMEZONE=UTC.
     - `install:20`: secrets. `sudo install -d -m 0711 /etc/tumnis/secrets`; the master key and pepper are JSON `{"active": 1, "keys": {"1": "<openssl rand -base64 32>"}}`; `tumnis_metrics_token` is `openssl rand -hex 32`. All owned 10001:10001, mode 0400. Skip each if it exists.
     - `install:30`: `[ -e .env ] || cp .env.example .env`, chmod 600; sed the three passwords to `openssl rand -hex 24` only where empty; sed TUMNIS_HOST and PUBLIC_BASE_URL.
     - `readme:manual`: TUMNIS_BIND_ADDRESS from `tailscale ip -4`, and `tailscale cert --cert-file /etc/tumnis/https/tumnis.crt --key-file /etc/tumnis/https/tumnis.key <name>`.
     - `install:40`: self-signed cert under sudo, skipped if one exists. `openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -days 825 -nodes -subj /CN=$TUMNIS_HOST -addext subjectAltName=DNS:$TUMNIS_HOST -addext basicConstraints=critical,CA:TRUE`. Key readable by Caddy (it runs as root in the container, so 0600 root is fine). Python 3.13 strict verify was tested OK with CA:TRUE+SAN; see /tmp/claude-1002/P4-06-c0/cert.sh.
     - `install:50 timeout=2400`: `docker build -f deploy/Dockerfile --build-arg VERSION=local -t ghcr.io/spaceshipcreative/tumnis:local .` (the GHCR image isn't accessible).
     - `install:60 timeout=1800`: `docker compose up -d --build --wait --wait-timeout 1500`.
     - `install:70`: curl `--cacert "$TUMNIS_CA" https://$TUMNIS_HOST/health/ready` loop, plus a grep of `<div id="root">` on `/`, with `expect="status":"(ok|degraded)"`. Info-string regexes cannot contain spaces; use `\s`.
   - **First run** section:
     - Browser path: `https://<host>/setup`, email, password ≥12, timezone, scan the QR, code.
     - Headless block `readme:first-run:10`: curl POST /v1/setup {email,password,timezone} → {otpauth_uri, setup_token}, then a python3-stdlib TOTP (SHA-1, 6 digits, 30 s), then POST /v1/setup/totp {setup_token, code}. Then sign in again: POST /v1/auth/login → preauth, wait for the NEXT 30 s step (replay protection), POST /v1/auth/totp. Print `Signed in as <email>`, expect `^Signed\sin\sas\s`. curl sends no Origin header, so the Origin check passes. Note cookies are Secure (https only).
   - Other sections (from the fact sheet below): Connect sources, Phone, Agents, Operate (link to docs/OPERATIONS.md), **Not yet available**, Develop.
     - Not yet available (decision 80): the deferred P3-01/03/04/05/06/07/11 connectors (Inbox Zero, Granola, chat, matching, proposal runs, Google Docs). State that Discord is the chosen first chat provider, with ADR-0014 to follow.
   - Use example.com / tumnis.example only.
3. Write **docs/INSTALL-CHECKLIST.md**: the second person's install, timing and friction per step, listing the manual blocks. Entry stays pending.
4. Write **docs/RELEASE-CHECKLIST.md**: the plan's 13 rows (see IMPLEMENTATION-PLAN-DETAILED.md, P4-06).
   - Evidence is a link or starts with "pending".
   - Rows for the second-person install, the restore drill and the signed tag MUST be pending.
   - T-06 only enforces "all links" on release branches (`on_release`).
5. Add the **CHANGELOG** entry: v1.0.0 content stays under `## [Unreleased]`, and a RELEASE-CHECKLIST row renames it at tag time (deviation: tagging is Scott's). Include the schema_version list and migration notes.
6. Add TODO(coordinator) comments in the docs for features not yet on main: P4-04, P3-02, P3-09, P3-12 (Obsidian), P3-13 (S3 *source*), P3-14 (#107/#113). P2-06 is now merged, so it needs none. Resolve each as the coordinator reports it merged.
7. Optionally: a deploy/README.md pointer to the standalone install, and `.gitignore` deploy/compose.local.yaml (the optional services overlay that OPERATIONS mentions).
8. Run make check, commit, push and open the DRAFT PR.
   - Remove the T-05/06/08 markers only after CI shows XPASS, citing the run id.
   - The readme.yml `readme-install` job is the real test bench; expect several pushes.
   - Diagnose a failing run from the artifact `readme-install-report` and the "Stack state and logs on failure" step.
9. Mark the PR ready, request CodeRabbit once, and run the loop per ~/tumnis-coordinator/pr-review-loop.md.
10. SendMessage to "main": `#<PR> MERGE-READY at <sha>`. Then do the final SubagentHandback.

## Fact sheet (what main actually does; from a survey of 21be4591)

- **Settings sections:** account (2FA, push toggle), sessions, keys, audit, dead-letters, agents (runners, Hermes profiles, tools), workspace, working-hours, calendar (Google OAuth client entered by the user, read-only scopes, 10-min sync), storage (server folder or S3 location), calibration, metrics, voice.
- **No UI:** GitHub (`PUT /v1/settings/github`: token, allowed_repos, webhook_secret; PR tracking), Coolify (`coolify`: base_url, token), planning, focus and triage.
- **Knowledge:**
  - Uploads up to 50 MiB: pdf, docx, xlsx, pptx, txt/md/csv, html, png/jpg/tiff/webp. ClamAV scan, then Docling.
  - Notes and links.
  - Storage locations: server_path and s3.
  - Tumnis-made project folders with two-way sync.
  - **SFTP is NOT implemented**, and "existing folder" mode is not creatable. **Obsidian is not on main** (P3-12 is in flight).
  - Integrations framework: only the scripted and google_calendar connectors.
- **Jev key:** no UI or API. The key actually used is `TYPESAFE_API_KEY` in each Hermes profile `.env` (`profiles/shared/jev-mcp`). The backend's TYPESAFE_API_KEY is only the preview guard.
- **Decision 75 (hosted keys in the server .env):** not implemented in code. `speech/hosted.py` says key storage is a "Scott item". This is a **Scott item**.
- **Agents (daemon/README.md):**
  - Runner created in Settings > Agents; token shown once.
  - /etc/tumnis/daemon.toml; systemd user `tumnis-agent`; dials `/ws/runner`.
  - Profiles: master and project-template, each with a .env.example (TUMNIS_URL, TUMNIS_TOKEN with the delegate scope, TYPESAFE_API_KEY, GITHUB_*, COOLIFY_*).
  - Discord: DISCORD_BOT_TOKEN, DISCORD_ALLOWED_USERS, DISCORD_ALLOWED_CHANNELS and DISCORD_HOME_CHANNEL in the master profile .env. "now" notifications go to Discord via `notification.ready`.
- **Phone:**
  - PWA. Push: Settings > Account > Enable push. On iOS, add to the Home Screen first (16.4+). VAPID keys are generated per workspace.
  - Voice: device Web Speech by default; Piper when `SPEECH__PIPER_URL` is set.
- **Optional env not in compose:** `GENERATION__*`, `EMBEDDINGS__*`, `SPEECH__PIPER_*` and `KNOWLEDGE__VISION_*` use `is None` checks, so they can't be passed as empty values. Document a user overlay `deploy/compose.local.yaml` with `COMPOSE_FILE=deploy/compose.yaml:deploy/compose.local.yaml`.
- **Day features:**
  - Focus levels quiet, nudge, coach and guardrail.
  - Daily plan (08:30 Mon–Fri) with accept, swap and remove.
  - Close the day from 16:00.
  - /review queue.
  - Digests are Hermes cron skills.
- **CLI:** api, worker, migrate [--check], seed, gen, mcp-stdio, drill record, keys rotate-master --to, audit verify, admin reset-totp, knowledge backup-sources, embeddings index, decisions eval. There is no `tumnis setup` (the web /setup page handles that).
- **Health:** /health/ready statuses are ok, degraded or down only (A4.4's "not_configured" doesn't exist). backups shows degraded while TUMNIS_BACKUPS=off. The harness `health_problems` requires postgres, dbos, schema and every module:* to be ok.

## Deviations so far (for the PR body)

1. Compose gains a `proxy` (Caddy) service under the `standalone` profile. Without it there is no HTTPS on a non-Coolify install. Bound to 127.0.0.1 or the Tailscale IP, so it passes T-P0-04-09.
2. `TUMNIS_BACKUPS` (default `on`): the install starts with archiving off, which avoids issue #21's archive-push crash without B2 keys. Locked pgbackrest tests are unchanged.
3. `PUBLIC_BASE_URL` is passed through x-env; Origin checks behind the proxy need it.
4. Secrets are owned by uid 10001, mode 0400, not the plan's `-o root -m 600`, which the service user can't read.
5. A4.4 step 4: Playwright @smoke is replaced by curl checks of the shell page plus the headless first-run block.
6. The health assertion tolerates `backups: degraded`. "not_configured" does not exist.
7. The CHANGELOG keeps v1.0.0 under [Unreleased] until Scott tags.
8. T-P4-06-08 (OPERATIONS upgrade suite plus the nightly upgrade job) is built. The job skips until a release tag contains scripts/readme_test.py.
9. The image is built locally (`tumnis:local`) because the GHCR image isn't accessible.

## Scott items

- Decision 75 hosted keys are not implemented in code. .env.example says so.
- The Jev key has no Settings UI; it lives only in the Hermes profile .env.
- SFTP storage is not implemented, although the plan and the coordinator prompt name it. Docs won't claim it.
- No Settings UI for GitHub, Coolify, planning, focus or triage; API only.
- The README install is unproven until the readme.yml CI run passes. The second person's install stays pending.

## Cited docs (for the PR body)

- Docker Compose: the `.env` file and `COMPOSE_FILE`/`COMPOSE_PROFILES`, and project directory resolution (Context7 /docker/docs plus docs.docker.com/compose/how-tos/environment-variables/envvars/).
- Caddy 2.11: `tls <cert> <key>`, `auto_https disable_redirects`, `admin off` (caddyserver.com/docs/caddyfile).
- Tailscale `tailscale cert` (tailscale.com/kb/1080/cli#cert).
- GitHub Actions: `on.push.tags`, `schedule`, `paths`.
- `gh release view --json assets`.

## Verify

```bash
cd backend && rtk proxy uv run pytest -q -p no:cacheprovider tests/meta/test_docs.py tests/meta/test_readme_test.py tests/meta/test_release_checklist.py tests/meta/test_release_assets.py tests/meta/test_release_workflow.py tests/deploy
python3 scripts/readme_test.py --list            # tagged blocks in README order
docker compose --env-file .env.example config -q   # from the repo root
```

Tooling notes:
- `rtk` condenses pytest output, so use `rtk proxy`.
- The worktree guard refuses compound commands that mix `cd` with git or grep pipes. Split them or use script files.
- Local HTTPS checks against Docker need the sandbox disabled, which goes through the permission prompt.
