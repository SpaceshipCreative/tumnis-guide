# HANDOFF: P1-03 Generation slot

Written at about 351k tokens on the coordinator's "HANDOFF NOW". Do not start new work beyond the steps below.

- PR: https://github.com/SpaceshipCreative/tumnis-guide/pull/58 (`[P1-03] impl: Generation slot`), branch `wp/P1-03`, base `main` (f256fa1). Single PR; the WP is size S, no `-impl-2`.
- Worktree: `/home/claude/Projects/Tumnis-Guide/.claude/worktrees/agent-a178b67113cacf83f` (branch renamed to `wp/P1-03`; the `.git/config` rename failed to lock, harmless).
- Alembic: no migration, no revision id.
- PR body file (still accurate except the integration line): `/tmp/claude-1002/p1-03-body.md`.

## Commits (all pushed to `origin/wp/P1-03`)

| SHA | Message |
| --- | --- |
| 2a6e7d1 | test(decisions): P1-03 spec tests (red) |
| 0315091 | feat(decisions): placeholder first action over the Generation fake (T-01, T-06) |
| 78bf7cd | feat(decisions): bound Generation calls by the configured timeout (T-02) |
| a0cb85c | feat(decisions): send only the capped title and project name to Generation (T-03) |
| 271b20c | feat(decisions): spoken form of a focus message through the Generation slot |
| 74237de | feat(decisions): VllmGeneration over the SSRF-guarded OpenAI-compatible client (T-05) |
| 121ffb3 | feat(decisions): protect the Generation slot with import-linter generation-callers (T-04) |
| 2cc14b5 | docs(plan): P1-03 names and GENERATION__* settings in Part A |
| 6781269 | fix(decisions): keep the Generation answer when usage metadata is malformed (CodeRabbit) |
| (this) | chore: P1-03 handoff |

All six spec markers (`xfail(strict=True, reason="spec:P1-03")`) are removed; every T-P1-03-01..06 test passes. No assertion was changed or weakened.

## Local test results (before the CodeRabbit fix commit 6781269 unless noted)

- `make check`: green (re-run after 6781269 also finished green; lint-imports 9 contracts kept, 952 unit passed, 2 skipped for missing node_modules at the time; `npm ci` was run afterwards).
- `make test` (unit + contract): 1070 passed.
- `npm --prefix frontend run typecheck`: green. No Vitest or Playwright specs belong to this WP.
- Integration (`make test-int`): NOT confirmed. The first run errored at setup of `test_local_setting_does_not_leak_across_pooled_transactions` (an HTTPError while starting a fixture, i.e. Docker or environment, not related to this WP; no decisions integration test changed). A second run was started in the background (task bci4yjtnz) after the fix commit; its output file was empty and no pytest process was alive when I stopped. Treat local integration as unverified; rely on the CI `integration` job, and rerun locally with bare `make test-int` if wanted (use `-n 2` via `cd backend` then bare `uv run pytest -q -n 2 -m integration`).

## CodeRabbit (PR #58)

- Review 1: one inline comment (id 4138380436) on `adapters/vllm_generation.py`: guard non-dict `usage` before `.get`. FIXED in 6781269 with `test_malformed_usage_keeps_the_answer` (list, str, int). Replied in-thread, thread `PRRT_kwDOUx-vCc6nS3XZ` RESOLVED, and CodeRabbit replied "thanks... Review thread resolved".
- Re-review (`@coderabbitai review` after the push): "No actionable comments were generated in the recent review". Unresolved threads: 0. New actionable comments: 0.
- Outstanding: none. If a later push happens, comment `@coderabbitai review` again.

## CI state (run 36632237629, head 6781269), at handoff

- success: lint, unit, contract, e2e, security, spec-guard, red-proof, traceability, performance, skills, GitGuardian, version-skew.
- in_progress: `integration`.
- pending: `preview` (expected: no homelab runner; leave it).
- The first run (36631394138, head 2cc14b5) had every job green except `integration`, which was cancelled.

Coordinator note (relayed): the CI `integration` job runs about 9:40 against a locked 10:00 budget and gets cancelled at 10:00 even when every test passes. That is main-wide and owned by branch `fix/ci-integration-budget`. If `integration` ends "cancelled" with every test passing in the log: do not touch budgets, note it, run `gh run rerun 36632237629 --failed` at most once, and carry on. Real integration test failures are mine.

## Exact remaining steps

1. `gh pr checks 58` (or `gh run view 36632237629 --json jobs`). When `integration` finishes:
   - passed: done.
   - cancelled with all tests passing (check `gh run view <id> --log` for the pytest summary): `gh run rerun 36632237629 --failed` once, note it in the report.
   - a real failure: `gh run view <id> --log-failed`, fix (TDD), `make check`, push, comment `@coderabbitai review`.
2. Edit the PR body's integration line (currently "running at PR open; result will be edited in here") with the final result: `gh pr edit 58 --body-file /tmp/claude-1002/p1-03-body.md` after changing the line. Keep the trailing blank line and the `🤖 Generated with [Claude Code](https://claude.com/claude-code)` footer.
3. If the coordinator says another PR merged: `git fetch origin && git merge origin/main`, re-run `make check` and the layers, push (`git push origin wp/P1-03`, never force). Likely conflicts: `backend/.importlinter`, `backend/tumnis/settings.py`, `backend/tumnis/worker.py`, `backend/tumnis/modules/decisions/adapters/{__init__,port,fake}.py`, `docs/IMPLEMENTATION-PLAN-DETAILED.md` Part A rows (P1-02 also edits decisions files).
4. Do NOT merge. Final report per the review-loop template.

## Decisions and deviations (also in the PR body)

1. Provider and timeout come from a process-wide holder, `decisions/generation_config.py`, set by `decisions.api.configure_generation(settings, net_policy=, provider=)`; the worker calls it at start (`worker.configure_generation`). The plan's import-time `PLACEHOLDER_TIMEOUT_MS = settings...` can't work because `Settings` needs DB URLs.
2. `generation-callers` (type `protected`) ignores `decisions.tests.**` importers and carries `broken_contract_guidance` naming the contract (import-linter prints only the contract `name`, and T-P1-03-04 wants `generation-callers` in the output).
3. `GenerationProvider` port lives in `adapters/port.py` (re-exported by `generation_api`); registry entry is `decisions.vllm_generation`, real adapter imported lazily via `importlib` like Jev. `api-never-calls-out` was renamed "The api process never imports Jev, its SDK or VllmGeneration" and also forbids `vllm_generation`.
4. TDD step 6 (share the HTTP client with `VllmDecisions`): P1-02 not merged, so the shared piece is `adapters/openai_compat.py` (`ChatEndpoint`, `message_text`) for P1-02 to reuse.
5. No hosted-provider refusal for local-only projects yet (no `local_decisions_only` until P1-02, only the local vLLM exists); `project_id` is used for logging.
6. The red commit also carried the port, `GenerationSettings`, the holder and `NotImplementedError` skeletons so mypy passes on the tests.
7. `spoken_focus_message` implemented now (500 chars in, 200 out, its own prompt and `spoken_timeout_ms`): chosen defaults, not in the plan.
8. `tests/meta/_lint_tree.py` (helper, no assertion changes) now creates every `tumnis.modules.*` module the real config names, because a protected contract refuses allowed importers absent from the graph; without it the P0-01 boundary tests broke.

## Shared-file edits

`backend/.importlinter` (modules-api-only ignore for `generation_api`, `api-never-calls-out` extended, new `generation-callers`), `backend/tumnis/settings.py` (`GenerationSettings`, `Settings.generation`, `env_nested_delimiter="__"`), `backend/tumnis/worker.py` (`configure_generation`), `docs/IMPLEMENTATION-PLAN-DETAILED.md` (Part A: 2 A11 rows, 1 A12 row). No `pyproject.toml`, `uv.lock`, `Makefile`, `AGENTS.md` or `frontend/package.json` changes.

## For Scott

- Recordings under `backend/tumnis/modules/decisions/tests/recordings/vllm_generation/` are synthetic (hand-written in the vLLM 0.12 OpenAI-compatible shape; `notes` says so). Replace with scrubbed recordings from the homelab vLLM.
- Deployment: set `GENERATION__BASE_URL` (LAN address, e.g. `http://<host>:8000`; the SSRF guard refuses loopback) and `GENERATION__MODEL` in Coolify. Until both are set the placeholder stays "First action pending". A vLLM `--api-key` isn't supported yet (would live in the `generation` `provider_configs` slot; follow-up).
- CI `integration` budget cancellation is main-wide (see coordinator note).

## Verify commands

```bash
# from the worktree root
make check
make test                       # unit + contract (bare)
npm --prefix frontend run typecheck
cd backend                      # own call, then bare:
uv run pytest -q -n 2 -m integration
uv run pytest -q -p no:randomly tumnis/modules/decisions/tests tests/meta/test_import_contracts.py
uv run lint-imports
# semgrep meta test needs writable paths:
export SEMGREP_SETTINGS_FILE=/tmp/claude-1002/sg_settings.yml SEMGREP_LOG_FILE=/tmp/claude-1002/sg.log SEMGREP_VERSION_CACHE_PATH=/tmp/claude-1002/sg_version
gh pr checks 58
```

Background watcher `beqikwr5h` (/tmp/claude-1002/watch58b.sh) may still be running; it only prints events and is harmless. The earlier watcher bv4bcpz4v was stopped.
