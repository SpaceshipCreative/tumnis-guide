# Release checklist: v1.0.0

The checks that must pass before `v1.0.0` is tagged (REL-4, SEC-7). Each row names its evidence: a link to a CI run, a release asset or a file. Until a check has passed, its evidence reads `pending`, followed by what is still needed.

The unit test `backend/tests/meta/test_release_checklist.py` (T-P4-06-06) reads this table. On any branch it requires all thirteen rows, each with a link or `pending`. On a `release/*` branch or a tag build it requires a link in every row and refuses `pending`, so the release cannot be cut with a row unproven. Work through it on a `release/1.0.0` branch, replacing each `pending` with its evidence link as the check passes ([docs/OPERATIONS.md, Releases](OPERATIONS.md#releases)).

| # | Check | Evidence |
| --- | --- | --- |
| 1 | A0 to A4 acceptance suites green on `main` | pending: link the `ci.yml` run on the release commit |
| 2 | README job green on a clean VM (A4.4) | pending: link the `readme.yml` `readme-install` run on the release commit |
| 3 | A second person installed from the README alone and filed the checklist | pending: their entry in [INSTALL-CHECKLIST.md](INSTALL-CHECKLIST.md) with the date, time taken and no open blockers |
| 4 | Security job clean: no high findings from pip-audit, npm audit, Trivy, Semgrep and gitleaks (SEC-7) | pending: link the `ci.yml` `security` job on the release commit |
| 5 | CycloneDX SBOM generated for the backend, frontend and image and attached to the release (SEC-7) | pending: link `sbom.cdx.json` on the GitHub release |
| 6 | OpenAPI spec and JSON Schemas attached; the `schema_version` values listed in the changelog (SAAS-1, FR-14.7) | pending: link `openapi-v1.0.0.json` and `schemas-v1.0.0.tar.gz` on the GitHub release (the release workflow's `verify-assets` step checks all three assets) |
| 7 | Rollback rehearsal from v1.0.0 to the last v0.x passes (P0-30, REL-4) | pending: link the `release.yml` rehearsal job of the tag build |
| 8 | Version skew: the previous daemon and MCP clients are accepted (REL-4) | pending: link the integration run on the release commit |
| 9 | Restore drill passed on its quarterly schedule (REL-1), latest run green | pending: link the latest `restore-drill.yml` run and quote its audit log row (`tumnis drill record`) |
| 10 | Hermes, skills and MCP servers pinned by version in `profiles/` (SEC-7) | pending: link the `VERSION` and `distribution.yaml` files of `profiles/master` and `profiles/project-template` at the release commit |
| 11 | Changelog complete: semantic version `1.0.0`, migration notes | pending: rename `## [Unreleased]` in [CHANGELOG.md](../CHANGELOG.md) to `## [1.0.0] - <date>` and link it |
| 12 | Traceability job: every v1 requirement has a test | pending: link the traceability comment on the release PR |
| 13 | Tag `v1.0.0` signed and pushed; release workflow published | pending: link the tag and the GitHub release |
