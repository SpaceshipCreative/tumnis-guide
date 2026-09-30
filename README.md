# Tumnis Guide

A daily task organizer for freelancers and solopreneurs that turns every project into a plan a human and a team of AI agents (Hermes) execute together, reached through one REST and MCP surface. It is a modular Python monolith (FastAPI, DBOS, Postgres 18) with a React PWA and a small runner daemon on the agent server.

- Product: [docs/PRD.md](docs/PRD.md)
- Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), decisions in [docs/adr/](docs/adr/)
- Build plan: [docs/IMPLEMENTATION-PLAN.md](docs/IMPLEMENTATION-PLAN.md) and [docs/IMPLEMENTATION-PLAN-DETAILED.md](docs/IMPLEMENTATION-PLAN-DETAILED.md)
- Rules for coding agents: [AGENTS.md](AGENTS.md)

## Install

Tumnis runs as one compose file (`deploy/compose.yaml`: migrate, api, worker, Postgres 18 with pgvector and pgBackRest, PgBouncer), deployed by Coolify from `main` after CI is green (`.github/workflows/deploy.yml` on the homelab runner). Configuration is environment variables for deployment-level settings only; secrets are root-owned files mounted read-only from `/etc/tumnis/secrets` (`MASTER_KEY_FILE`, `API_KEY_PEPPER_FILE`). No port is published on the host: the api is reached through Coolify's proxy over the VPN or Tailscale.

Without Coolify, on any Docker host:

```bash
docker build -f deploy/Dockerfile -t ghcr.io/spaceshipcreative/tumnis:local .
export TUMNIS_VERSION=local APP_DB_PASSWORD=$(openssl rand -hex 24) \
  OWNER_DB_PASSWORD=$(openssl rand -hex 24) POSTGRES_PASSWORD=$(openssl rand -hex 24)
sudo mkdir -p /etc/tumnis/secrets
docker compose -f deploy/compose.yaml up -d --wait
```

The local stack with fakes and the seed set (the one CI's end-to-end job and Playwright use):

```bash
make up     # build and start deploy/compose.test.yaml; api on 127.0.0.1:8080 (TUMNIS_TEST_PORT moves it)
make down   # stop it and delete its volumes
```

Every process refuses to start (exit 78) on a configuration that would put a preview near production: `DEPLOYMENT_ENV=preview` needs `TUMNIS_ADAPTERS=fake` and no Jev key, and every process checks the database's `deployment_marker` against `DEPLOYMENT_ENV`.

The runner daemon installs separately on the agent server; see [daemon/README.md](daemon/README.md). Give each client project's agent profile its own credentials: a fine-grained GitHub token limited to that project's repos, and an API token from that project's own Coolify team. The profile health check (every 15 minutes, and on demand in Settings > Agents) marks a profile degraded when a token reaches another project's repo or app, or when it has an MCP server outside the project's tool allowlist.

## Operate

Health is at `/health/live` (no I/O; the `Tumnis-Version` header names the running build) and `/health/ready` (503 when Postgres or DBOS is down or the database is behind this release's migrations, 200 `degraded` when only a module check fails); metrics at `/metrics` (P0-27). Backups use pgBackRest with a quarterly restore drill (`scripts/drill/restore_drill.sh`, TBD).

**Master key.** Per-workspace settings and secrets are sealed in Postgres with each workspace's data key, which is wrapped by the master key in `MASTER_KEY_FILE`: JSON `{"active": 1, "keys": {"1": "<base64 of 32 random bytes>"}}`. The api and worker refuse to start (exit 78) when the file is readable by group or others or, in prod, owned by anyone but root or the service user (uid 10001): keep `/etc/tumnis/secrets/` root-owned 0o700 and the file `chown 10001:root`, mode 0o400, bind-mounted read-only. Back the key file up with the API key pepper file, off the database host: losing either makes every secret unreadable (ADR-0010).

Rotate the master key without downtime:

1. Generate a key (`openssl rand -base64 32`), add it to the file as the next version and point `active` at it.
2. Restart the api and the worker: new data keys are wrapped under the new version, and both versions stay loaded.
3. Run `tumnis keys rotate-master --to <new version>` with `DATABASE_OWNER_URL` set: it re-wraps every data key; the sealed values do not change.
4. Remove the old version from the file and restart again.

**Releases and rollback.** Versions follow [SemVer](https://semver.org) (`v0.1.0` at the phase 0 gate) and every release has a dated section in [CHANGELOG.md](CHANGELOG.md) ([Keep a Changelog](https://keepachangelog.com/en/1.1.0/)). Each PR with a user-visible change adds a line under `## [Unreleased]`. To release:

1. Rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`, add an empty `## [Unreleased]` above it, and merge that PR.
2. Check the tag locally: `python scripts/release/check_changelog.py vX.Y.Z`.
3. Tag `main` and push the tag: `git tag -s vX.Y.Z -m "..." && git push origin vX.Y.Z`.
4. `.github/workflows/release.yml` rehearses the rollback from the previous tag on the homelab runner, then pushes `ghcr.io/spaceshipcreative/tumnis:vX.Y.Z` and creates the GitHub release with the changelog section as notes, the CycloneDX SBOM (`sbom.cdx.json`) and the OpenAPI spec (`openapi-vX.Y.Z.json`) attached.

Rollback is "redeploy the previous image": point `TUMNIS_VERSION` back at the previous tag (or `sha-<commit>`) in Coolify and deploy. Migrations are expand-only (squawk checks them, and the rehearsal proves them), so the previous release runs on the newer schema: its `tumnis migrate` exits 0 with "database is ahead of this release (rollback)", and readiness treats a database ahead of the release as ready. Never roll the database back. DBOS resumes a workflow only on the application version that started it: when rolling back with work in flight, keep one worker on the newer image running until its queues drain. Rehearse any release locally with `scripts/release/rollback_rehearsal.sh <previous tag> HEAD` (Docker; the stack is `deploy/compose.test.yaml` as project `tumnis-skew` on 127.0.0.1:18080). Every PR that changes a migration runs the same rehearsal from the latest `main` image (`.github/workflows/version-skew.yml`).

## Develop

Prerequisites: [uv](https://docs.astral.sh/uv/) (Python 3.13), Node.js 22 or later with npm, [pre-commit](https://pre-commit.com/), and Docker for integration tests.

```bash
cd backend && uv sync          # Python toolchain and dependencies
cd ../frontend && npm ci       # ESLint, Prettier, TypeScript
cd .. && pre-commit install    # run the hooks on every commit
make check                     # lint, typecheck and unit tests; run before every commit
```

Other targets, filled in as work packages land:

| Command | Does | Status |
| --- | --- | --- |
| `make test` | Unit and contract tests | TBD (P0-02) |
| `make test-int` | Integration tests against Postgres and containers | TBD (P0-02) |
| `make e2e` | Playwright journeys against the seeded app with fakes | TBD (P0-22) |
| `make gen` | Regenerate OpenAPI, JSON Schemas and the TypeScript client | TBD (P0-11) |
| `make seed` | Load the seed set | TBD (P0-02) |

Work follows the TDD rules in [AGENTS.md](AGENTS.md): spec tests land red in a `wp/<WP>-spec` PR, then code turns them green in `wp/<WP>-impl`.
