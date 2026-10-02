# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Repository layout, toolchain and agent rules (P0-01): AGENTS.md, CLAUDE.md, ADRs 0001 to 0011, frontend lint and typecheck scaffold, daemon project skeleton.
- Releases (P0-30): `v*.*.*` tags run `.github/workflows/release.yml` (rollback rehearsal, changelog check, image, CycloneDX SBOM and OpenAPI spec on the GitHub release); `scripts/release/check_changelog.py` and `scripts/release/rollback_rehearsal.sh`; the `version-skew` workflow rehearses every PR that changes a migration.
- Version skew (P0-30): `tumnis migrate` exits 0 when the database is ahead of the release (a rollback); `/health/ready` has a `schema` check that is down only while the database is behind; `/health/live` names the build in the `Tumnis-Version` header.
- Agent surface (P2-01): MCP tools at `/mcp` with a REST twin for each (`list_tasks`, `create_task`, `update_task_status`, `update_estimate`, `get_project_context`, `search`), the tool catalogue `schemas/mcp/v1/tools.json`, and `tumnis mcp-stdio` for stdio-only clients.
- Install and operations docs (P4-06): the README rewritten around a tested install (Docker Compose, an HTTPS proxy, Tailscale or a self-signed certificate) and a first run; `docs/OPERATIONS.md` (health, backups, restore drill, upgrade, rollback, master key, certificates), `docs/INSTALL-CHECKLIST.md` and `docs/RELEASE-CHECKLIST.md`. `scripts/readme_test.py` runs the README's tagged blocks, and `.github/workflows/readme.yml` runs the install and first run on a fresh Ubuntu 24.04 VM (A4.4) and, nightly, the upgrade from the previous release and the rollback. The release workflow also attaches the JSON Schemas (`schemas-vX.Y.Z.tar.gz`) and fails when an asset is missing.
- Standalone deployment (P4-06): the compose profile `standalone` adds a Caddy HTTPS proxy on one private address; `.env.example` lists every deployment variable; `TUMNIS_BACKUPS=off` keeps WAL archiving and the backup service idle until the off-site repository is set up.
- The v1 product: projects, tasks and the board; the daily plan and close the day; focus levels from quiet to guardrail, with stuck help and detour capture; the review queue; knowledge bases with uploads (virus scan and extraction), notes, links, storage locations (server folder, mounted share, S3, SFTP), existing folders as a project's folder (Tumnis writes only in its `Tumnis/` subfolder) and the folder move job, and hybrid search; Hermes agents through the runner daemon, with runs, questions and approvals, delegation, digests, the kill switch and runaway limits; the calendar; GitHub and Coolify tracking; browser push, Discord delivery and voice; audit log, metrics, backups and the restore drill.

### Schema versions

- JSON Schemas (`schemas/`, attached to each release): `api`, `digest`, `enrichment`, `entities`, `events`, `mcp` (the tool catalogue), `packet`, `planning` and `result` are all at `schema_version` 1 (`v1/`). The runner protocol has versions 1 and 2 (`runner/v1/`, `runner/v2/`); the server accepts a daemon that speaks only protocol 1.

### Migration notes

- A new install needs no manual step: `migrate` runs first on every start, and the api and workers wait for it. Every migration is expand-only, so the previous release keeps working on the newer schema; roll back by redeploying the previous image, never by downgrading the database ([docs/OPERATIONS.md](docs/OPERATIONS.md#rollback)).
- Releasing: v1.0.0 stays under `## [Unreleased]` until the tag; renaming this section is row 11 of `docs/RELEASE-CHECKLIST.md`.
