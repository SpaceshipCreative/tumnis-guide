# Tumnis Guide

A daily task organizer for freelancers and solopreneurs that turns every project into a plan a human and a team of AI agents (Hermes) execute together, reached through one REST and MCP surface. It is a modular Python monolith (FastAPI, DBOS, Postgres 18) with a React PWA and a small runner daemon on the agent server.

- Product: [docs/PRD.md](docs/PRD.md)
- Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), decisions in [docs/adr/](docs/adr/)
- Build plan: [docs/IMPLEMENTATION-PLAN.md](docs/IMPLEMENTATION-PLAN.md) and [docs/IMPLEMENTATION-PLAN-DETAILED.md](docs/IMPLEMENTATION-PLAN-DETAILED.md)
- Rules for coding agents: [AGENTS.md](AGENTS.md)

## Install

TBD (P0-04). Tumnis runs as one compose file (`deploy/compose.yaml`) deployed by Coolify from `main`. Configuration is environment variables for deployment-level settings only; secrets are root-owned files mounted read-only (`MASTER_KEY_FILE`, `API_KEY_PEPPER_FILE`).

```bash
make up     # TBD (P0-04): start the stack locally
make down   # TBD (P0-04): stop it
```

The runner daemon installs separately on the agent server; see [daemon/README.md](daemon/README.md).

## Operate

TBD. Health is at `/health/live` and `/health/ready`, metrics at `/metrics` (P0-04 onward). Backups use pgBackRest with a quarterly restore drill (`scripts/drill/restore_drill.sh`, TBD).

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
