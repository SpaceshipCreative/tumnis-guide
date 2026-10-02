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
- Docling CI (Scott decision 85): the optional `docling` dependency group and `.github/workflows/docling.yml` run the real-Docling tests (`pytest -m docling`) with cached models; the image does not include the group.
- Standalone deployment (P4-06): the compose profile `standalone` adds a Caddy HTTPS proxy on one private address; `.env.example` lists every deployment variable; `TUMNIS_BACKUPS=off` keeps WAL archiving and the backup service idle until the off-site repository is set up.
- The v1 product: projects, tasks and the board; the daily plan and close the day; focus levels from quiet to guardrail, with stuck help and detour capture; the review queue; knowledge bases with uploads (virus scan; Docling text extraction is not in the image yet, so on an install files end `extraction_failed`), notes, links, storage locations (server folder, mounted share, S3, SFTP), existing folders as a project's folder (Tumnis writes only in its `Tumnis/` subfolder) and the folder move job, Obsidian vaults read from a server folder or a Git remote with a read-only deploy key (P3-12), S3 buckets (MinIO, Backblaze B2, other S3-compatible) as read-only linked sources (P3-13), and hybrid search; retention for ingested email, chat and notes, and purges of a connection's content or an archived project (P3-09); Hermes agents through the runner daemon, with runs, questions and approvals, delegation, digests, unattended overnight runs in a set window, the kill switch and runaway limits; the calendar; GitHub and Coolify tracking; browser push, Discord delivery and voice; audit log, metrics, backups and the restore drill.

### Not in this release

- Deferred until they can be tested against the real services: the provider learning tests (P3-01), the Inbox Zero (P3-03), Granola (P3-04) and chat (P3-05) connectors, matching what they bring in to tasks (P3-06), proposal runs (P3-07) and Google Docs as a source (P3-11). Settings > Connections is in place with no provider to connect yet. Discord is the chosen first chat provider (ADR-0014 will record it when the connectors resume).
- Text extraction with Docling is not in the image: on an install, an uploaded or synced file is scanned and then ends `extraction_failed`.

### Schema versions

- JSON Schemas (`schemas/`, attached to each release): `api`, `digest`, `enrichment`, `entities`, `events`, `mcp` (the tool catalogue), `packet`, `planning` and `result` are all at `schema_version` 1 (`v1/`). The runner protocol has versions 1 and 2 (`runner/v1/`, `runner/v2/`); the server accepts a daemon that speaks only protocol 1.

### Migration notes

- A new install needs no manual step: `migrate` runs first on every start, and the api and workers wait for it. Migrations expand the schema first, and a later contract migration (such as `integrations_0004`) removes only what the previous release no longer uses; every release's rollback rehearsal deploys N, N+1, then N again on one database. Roll back by redeploying the previous image, never by downgrading the database ([docs/OPERATIONS.md](docs/OPERATIONS.md#rollback)).
- Releasing: v1.0.0 stays under `## [Unreleased]` until the tag; renaming this section is row 11 of `docs/RELEASE-CHECKLIST.md`.
