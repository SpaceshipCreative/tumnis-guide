# HANDOFF: P2-10 Tool allowlists and credential scoping

Stopped on "HANDOFF NOW" from the context watcher. Branch `wp/P2-10` is pushed (push with
`/usr/bin/git push origin HEAD:wp/P2-10`). **No PR has been opened yet.**

## Commits (on top of main 989c6c6)

| SHA | What |
| --- | --- |
| 09a8219 | `test(agents): P2-10 spec tests (red)`: all 9 spec tests as strict xfail, plus daemon deps (httpx, pyyaml; dev: respx, hypothesis, types-pyyaml) and a `Drift`/`allowlist_drift` stub |
| 6201f9d | rules: `allowlist_drift`, `TokenReach`, `ReachVerdict`, `reach_verdict`, `profile_health`, `github_repo`, `reach_targets` (+ unit tests). T-P2-10-02 green |
| b37b423 | daemon: `tumnis_daemon/health.py`, protocol fields, `hermes_home` config, runner `health_report` fills the P2-10 fields; backend `protocol.py` fields; runner golden fixtures + `schemas/runner/v1` regenerated. T-P2-10-01, 04, 05, 07 green |
| 7edaffe | backend: `check_profile_health` drift/reach evaluation, `review_kinds.py` (`drift`), `GET /v1/agents/profiles/{profile_id}/tools`, `profile_health_sweep` every 15 min via `agents.workflows.schedules()`, projects api `links_of_kind` (identical to P2-14's), `code_repos`, `project_names`, `tool_allowlist`, projects rules `TOOL_ALLOWLIST_DEFAULT`/`effective_tool_allowlist`; `make gen` output |

## Spec tests

| ID | State |
| --- | --- |
| T-P2-10-01, 02, 04, 05, 07 | green, markers removed (ran XPASS(strict) first, then removed) |
| T-P2-10-03, 06, 08 (`backend/tumnis/modules/agents/tests/integration/test_profile_health.py`) | code written, **markers still on, never run** (Docker-only). Run `make test-int` (bare) or rely on CI; if XPASS(strict), remove the 3 markers |
| T-P2-10-09 (`frontend/src/components/settings/AgentTools.test.tsx`) | `test.fails`, **component not written yet** |

Local results at 7edaffe: backend unit layer 1104 passed (`-n 3`); daemon 21 passed; mypy, ruff, lint-imports green; non-Docker contract tests pass. Integration and Docker contract layers not run locally.

## Remaining steps

1. Run the integration layer (`make test-int`, bare) or push and read CI; fix `test_profile_health.py` failures if any; remove the 3 markers only after they pass. Also check the earlier P1-04 health tests (`test_profiles_api.py`, `test_ws_runner.py`) stay green.
2. Write `frontend/src/components/settings/AgentTools.tsx` (props `{ profileId: string }`), using the generated `agentsGetProfileToolsOptions` (check the exact name in `frontend/src/api/@tanstack/react-query.gen.ts`). The spec test needs: a list named "MCP servers" whose items are labelled by server name, "Allowed"/"Not allowed" text, `data-drift="extra"` on disallowed items only, a list named "Missing servers", the foreign repos text (e.g. `beta/app`), "Hermes 0.9.1", "profile 1.0.0", and no buttons, textboxes, checkboxes or comboboxes. Mount it inside `AgentsSection.tsx`'s `ProfileItem` (e.g. a `<details>` so the section's own "Check health" button stays out of AgentTools). Update `healthChip` in AgentsSection for `degraded` ("Degraded", BAD) and `warning`. Then flip `test.fails` to `test` after a passing run, optionally switch the loader to a static import. Check 375 px width.
3. When P2-14 (#62) merges: `git merge origin/main` (projects `links_of_kind` is byte-identical to P2-14's, so it should merge clean; if it conflicts keep one copy). Then replace the `importlib`/`getattr` seam `_coolify_base_url` in `agents/workflows.py` with a static `from tumnis.modules.coolify import api as coolify` and `coolify.get_settings(ctx)`. When P2-13 (#68) merges, merge main again (no code dependency).
4. README agent section: document one Coolify team per client project (plan implementation note), `hermes_home`, and the `.env` names the probes read (`GITHUB_TOKEN`/`GITHUB_PERSONAL_ACCESS_TOKEN`, `COOLIFY_TOKEN`/`COOLIFY_API_TOKEN`, `COOLIFY_BASE_URL`/`COOLIFY_URL`). Also `daemon/README.md` layout table (health.py) and config example.
5. `make check` (with the SEMGREP_* env vars below), backend unit/contract/integration `-n 3`, Vitest `--maxWorkers=4`.
6. Open the PR (body in $TMPDIR, cite the docs below, list deviations and Scott items), `gh pr comment <url> --body "@coderabbitai review"`, run the review loop in `~/tumnis-coordinator/pr-review-loop.md`.

## Decisions and deviations (put these in the PR body)

- **tool_allowlist default:** T-P0-17-17 (locked) asserts a new project's policy row stores `tool_allowlist == []`, so the template default is applied on read (`projects.rules.effective_tool_allowlist`: empty means the template's `tumnis, jev, github, coolify`) instead of being written at create. An explicitly empty allowlist therefore cannot mean "no servers".
- **MCP server source:** the plan says `config.yaml` key `mcp_servers` (verify against Hermes). Hermes docs confirm `mcp_servers` in config.yaml, and profile distributions (P1-05's `profiles/*/mcp.json`) ship `mcpServers` in `mcp.json`. The daemon reads both; config.yaml wins on a name clash.
- **Coolify token exfiltration guard (addition):** the server supplies `coolify_base_url`; when the profile's `.env` names `COOLIFY_BASE_URL`/`COOLIFY_URL`, the daemon only probes at that same origin. Without one, it trusts the server's URL (residual risk: a compromised server could learn a Coolify token; GitHub tokens only ever go to the hard-coded api.github.com).
- **Master profile:** no project, so no allowlist and no drift; it still gets token reach probes with every project's repos and apps as foreign.
- **Drift evaluated only for runner reports** (not MCP-endpoint profiles, which report no servers).
- **Schedule:** registered through `agents.workflows.schedules()` (picked up by `worker.register_module_schedules`), not a new function in `worker.py`; queue `agents-sweep`.
- **Review item:** one `drift` item per distinct finding (extra servers plus foreign reach), dedupe key `drift:<profile>:<sha256[:32]>`; target type `agent_profile`; payload names the other project (`project_name`) and a fix sentence.
- **Project card:** T-P2-10-08 checks the profile list (Settings) and the tools route; the dashboard project card has no agent section yet, so no card change was made.
- **Daemon test file location:** `daemon/tests/test_health_probe.py` at the tests root, as the plan names it (other daemon tests live under unit/ and integration/).
- **Coolify base URL seam:** until P2-14 lands, `_coolify_base_url` looks up `coolify.api.get_settings` by name and returns None when absent (the daemon then reports `coolify: no usable base URL` as a warning).
- Shared-file edits so far: none of the listed shared files (`backend/pyproject.toml`, `uv.lock`, Makefile, AGENTS.md, .importlinter, frontend/package.json). `daemon/pyproject.toml` and `daemon/uv.lock` gained httpx==0.28.1, pyyaml==6.0.3 (runtime) and respx==0.22.0, hypothesis==6.168.3, types-pyyaml==6.0.12.20260906 (dev), pinned to the backend's versions. The runner golden fixtures `backend/tests/contract/fixtures/runner/{health_check,health_report}/v1.json` gained the new fields (the daemon contract test round-trips them).
- No alembic revision.

## Scott items

- Confirm the residual Coolify-token risk above is acceptable, or require `COOLIFY_BASE_URL` in every profile `.env` (then the probe refuses when it is missing).
- Confirm the "empty allowlist means the template's" reading (keeps T-P0-17-17 unchanged).
- Done checklist item needs the homelab: "Scott's profiles show no drift and no foreign reach".

## Docs checked (cite in the PR body)

- Context7 `/nousresearch/hermes-agent`: `mcp_servers` in config.yaml (command/args/env or url/headers); per-profile `.env` at `~/.hermes/profiles/<name>/.env`; distributions ship `mcp.json` (docs/user-guide/features/mcp.md, profile-distributions.md, hermes_cli/profile_distribution.py).
- Context7 `/coollabsio/coolify-docs` and https://coolify.io/docs/api-reference/authorization: tokens belong to the user and active team, `Authorization: Bearer`, `root` does not reach other teams (api/permissions.mdx).
- https://docs.github.com/en/rest/repos/repos#get-a-repository: 200/301/403/404, `permissions.{admin,push,pull}` in the answer.
- https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens: fine-grained tokens are limited to one owner and selected repos, and "always include read-only access to all public repositories".
- Context7 `/lundberg/respx` (0.22.0): `respx.mock(base_url=..., assert_all_called=...)`, `.respond()`, `side_effect=httpx.ConnectError`.

## Gotchas

- rtk condenses pytest output ("No tests collected"); use `rtk proxy uv run pytest ...`.
- Plain `git` is refused; use `/usr/bin/git`. Heredocs with `cat >>` and grep patterns with `|` are refused by the worktree guard; use the Write/Edit tools and `grep -e`.
- `~/.semgrep` is read-only: `SEMGREP_SETTINGS_FILE=/tmp/claude-1002/semgrep/settings.yml SEMGREP_LOG_FILE=/tmp/claude-1002/semgrep/semgrep.log SEMGREP_VERSION_CACHE_PATH=/tmp/claude-1002/semgrep/version PYTEST_XDIST_AUTO_NUM_WORKERS=3 make check`.
- Under VM load (load average ~45), `make check`'s Vitest run times out in unrelated files; `npx vitest run --maxWorkers=4` had 2 load flakes (render.test.tsx, TaskDrawer.test.tsx).

## Verify

```bash
cd daemon && rtk proxy uv run pytest -q tests
cd backend && rtk proxy uv run pytest -q -n 3 -m "not integration and not contract"
cd backend && uv run mypy tumnis && uv run lint-imports
make test-int          # bare, from the worktree root (Docker)
cd frontend && npx vitest run src/components/settings --maxWorkers=4
```
