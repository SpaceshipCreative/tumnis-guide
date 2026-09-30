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
