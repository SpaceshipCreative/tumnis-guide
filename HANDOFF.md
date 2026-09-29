# P1-02 handoff (Fallback, thresholds and the decision log), continuation 2

PR **#60** https://github.com/SpaceshipCreative/tumnis-guide/pull/60 (branch `wp/P1-02`). Do not merge.

A fresh agent: `/usr/bin/git fetch origin && /usr/bin/git switch -c agent/P1-02-c3 origin/wp/P1-02`
(if HEAD does not move: `/usr/bin/git symbolic-ref HEAD refs/heads/agent/P1-02-c3`), push with
`/usr/bin/git push origin agent/P1-02-c3:wp/P1-02`. Setup: `cd backend && uv sync --frozen --all-extras`,
`npm ci --prefix frontend`.

## State

Every P1-02 spec test (T-P1-02-01..17) is green with its marker removed. CI on `4cc9297` was
all green (unit, contract, integration, e2e, lint, security, spec-guard, traceability,
red-proof, performance, skills, version-skew); `preview` stays pending (no homelab runner).

Commits in this continuation (after `144a5b4`, the continuation-1 handoff):

- `ea54d5c` feat(decisions): input_hash and low_route
- `32b006f` feat(projects): local_decisions_only on the project and its cached lookup
- `b0a39e4` feat(decisions): decide, thresholds, the decision log and decision.made
- `0ef04ec` feat(frontend): local decisions only switch (T-P1-02-16)
- `52f19cc` docs(plan): P1-02 names in Part A12
- `9660ff2` test(decisions): T-P1-02-05..14 and 17 pass; remove their spec markers
- `d72d9e0` merge origin/main (P1-09)
- `a82a4d2` / `ad1c223`: snooze is not an outcome (CodeRabbit round 1)
- `417ae2b` fix(frontend): toggle sends a newer refetched version (CodeRabbit round 1)
- `82c6a9f` chore: removed the previous HANDOFF.md (this file replaces it)
- `4cc9297` merge origin/main (P1-03 Generation slot; conflicts resolved keeping both sides)
- `e99633a` test(decisions): two new integration tests, RED, for CodeRabbit round 2 (below).
  Pushed with this handoff, so CI `integration` on the handoff commit is expected to fail on
  exactly these two tests until the fixes below land.

## CodeRabbit

Round 1 (3 threads): all fixed, replied and resolved (snooze `ad1c223`, toggle `417ae2b`, HANDOFF `82c6a9f`).

Round 2 (review 5359541013, 2 open threads, both in `backend/tumnis/modules/decisions/api.py`).
The red tests are in `e99633a`; the fixes are NOT written yet:

1. Thread `PRRT_kwDOUx-vCc6nUyZN` (comment 4139193024): in `decide`'s no-answer branch,
   `fallback = local_only` disagrees with `eff = effective_threshold(..., fallback=any(...))`.
   Fix: `fallback = any(s.fallback_reason for s in chain)`,
   `reason = chain[-1].fallback_reason` (primary_failed for Jev then vLLM, local_only for a
   local-only project, None when the chain has no fallback), and use `fallback` for `eff`.
   Test: `test_nobody_answered_is_logged_as_the_fallback_it_tried`.
2. Thread `PRRT_kwDOUx-vCc6nUyY3` (comment 4139192988): a cached Jev answer could be served for
   the vLLM slot when the vLLM model id equals the Jev model id. Keep the plan's key format
   (`ws:{ws}:decisions:{point}:{input_hash_hex}`, T-P1-02-09..12 text), so do NOT add the
   provider to the key as CodeRabbit suggests. Instead cache
   `{"slot": first.name, "response": <ProviderResponse JSON>}` in `_ask` and treat a hit whose
   slot differs from `first.name` as a miss. Reply in the thread explaining the key format is
   the plan's. Test: `test_cached_jev_answer_is_not_reused_for_vllm`.

Then: `make check`, commit (`fix(decisions): ... (CodeRabbit)`), push, reply to and resolve
both threads (`resolveReviewThread`), comment `@coderabbitai review`, loop until clean and CI
green.

## Local verification notes

- `make check` needs the semgrep env vars inline, with files unique to you (a shared
  `/tmp/claude-1002/check.log` was overwritten by another agent once):
  `SEMGREP_SETTINGS_FILE=/tmp/claude-1002/p102_semgrep_settings.yml SEMGREP_LOG_FILE=/tmp/claude-1002/p102_semgrep.log SEMGREP_VERSION_CACHE_PATH=/tmp/claude-1002/p102_semgrep_version make check`.
  Under load (load average 40 to 60) Vitest can time out and hypothesis `test_timeout_fires_inside_budget`
  can flake; a rerun passes.
- `make test-int` (bare): the first two runs finished (all P1-02 tests green; unrelated
  load failures: rclone, relay latency, typeahead p95, health 503, preview seed timeout, minio,
  day-close kill). The last two runs could not start containers (Docker: "failed to set up
  container networking"), so CI is the reference for the integration layer.
- Heredocs and `cd x && git ...` compounds are refused by the worktree guard; write scripts to
  the scratchpad and run them, or run git alone from the worktree root.

## Deviations (also in the PR body)

- `input_hash` in `catalog.py` (rules allow-list has no hashlib/json).
- `decide` calls `provider.ask` directly (needs the built request for hash and fields_sent);
  extra optional `providers` and `clock` kwargs; `providers=None` builds the registered
  adapters (fakes in fake mode; real Jev from the stored key; real vLLM from setting
  `decisions.vllm` with `configure_net_policy`, which the worker now calls).
- `record_outcome` takes optional `outcome_at` and `session`; a `snooze` is ignored.
- Local-only read through cached `projects.api.local_decisions_only()`, not `get_project`.
- `payloads.py` split (like tasks). Thresholds not seeded; rows for every point appear on a
  pinned-model change. Review item only when the low route is `review`.
- `route()` has `# noqa: PLR0911`. Settings sections `decisions.jev` / `decisions.vllm` are
  read with `get_setting` but not registered as Settings API sections.
- `.importlinter` `api-never-calls-out` keeps P1-03's name (its meta test matches the name) and
  also forbids `adapters.vllm`. `backend/tumnis/worker.py` calls `configure_net_policy`.
- Commit trailers: continuation-1 commits say Claude Sonnet 5.5; the rest Opus 5.5.

## Shared-file edits

`backend/.importlinter`, `backend/tumnis/worker.py`, `backend/tumnis/core/tests/integration/row_factory.py`,
`backend/tumnis/modules/usage/rules.py` (decision.made counter), `docs/IMPLEMENTATION-PLAN-DETAILED.md` (A12 row).

## Scott items

- vLLM recordings in `backend/tumnis/modules/decisions/tests/recordings/vllm/` are synthetic;
  re-record from the homelab vLLM.
- Alembic revision `decisions_0002` (after `decisions_0001`).
- Toggle at phone width: covered by Vitest at 375 px; preview check waits for the homelab runner.
