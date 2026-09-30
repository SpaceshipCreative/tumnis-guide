# HANDOFF: ci-health (c0 → c1)

Task prompt: /tmp/claude-1002/coord7/ci-health.txt (binding). Branch `fix/ci-health`, **draft** PR #112
(https://github.com/SpaceshipCreative/tumnis-guide/pull/112). CodeRabbit: not yet requested (draft, so it
skipped). Scratch: `$TMPDIR/ci-health-c0/` (logs, helper scripts `jobs.py`, `t01.py`, `durs.py`,
`waitjob.sh`).

## Commits (oldest first)

| SHA | What | Keep? |
| --- | --- | --- |
| f977356 | ci(diag): TEMPORARY T-01 stage timings, `--durations=0`, `.github/workflows/diag.yml` | REVERT |
| 5dad9f4 | ci(diag): TEMPORARY GC pauses, gc.freeze A/B, py-spy (`tests/_diag_prof.py`) | REVERT |
| 00405da | ci(diag): TEMPORARY per-fixture timings (`tests/_diag_fix.py`) in the integration step | REVERT |
| ab629c2 | fix(harness): `pytest_collection_finish` → `gc.collect(); gc.freeze()` in `backend/tests/fixtures/__init__.py` + `backend/tests/harness/test_collection_frozen.py` (red without the fix, verified) | KEEP |
| 2d86610 | perf(app): `create_app` includes each /v1 router once under `prefix="/v1"` (`v1_routes` → `v1_routers`) | KEEP (not yet measured in CI) |

## Problem 1: T-P1-07-01 root cause (proven)

- One full (gen-2) GC, 315–340 ms over ~477k objects that collecting the whole suite leaves (the serial step collects all
  3,236 tests, then deselects to one), lands inside the 8 s measurement. That pauses api, relay and every label in flight
  at once, and they become the p95 tail. GC callback timestamps match the event-loop stalls exactly (2.34 s/337 ms ↔
  lag 373 ms; 5.34 s/315 ms ↔ 319 ms).
- The test was always marginal: quiet-runner p95 was 889–975 ms before the fix. #93's call durations match
  post-#93 (5.7–7.8 s); P1-07 c2 saw 1,062/1,551 ms before the serial step. The heap grows with every merged test module.
- Ruled out: leftover containers/Ryuk (docker ps is empty at the serial step start), subscriber fan-out (6 queued deliveries per task,
  all pre-#93: search, usage, tasks.refresh_review_impact*), queue polling, the direct-subscriber lead on main (only
  decisions.label_on_create is direct; commit→relay p50 ~22 ms, relay→wf ~20 ms). That lead is real for P1-08's branch.
- Stage breakdown (quiet, before): POST-in-txn p50 87 ms, relay ~45 ms, load 66, decide 375 (fake 250), apply 38.
- T-01 samples: BEFORE: post-parallel p95 1,057 ms (FAIL, run 36780576339); quiet 889, 974, 950, 950, 964, 984,
  1,117 (FAIL). Main failures: 1,007 (36776950419), 1,208 (P1-08), 1,083 (P2-00-spec).
  AFTER freeze (ab629c2): post-parallel p95 768.5 ms (max 858, gen-2 n=0; run 36783539951); quiet 620, 607,
  621, 640 ms (run 36783539820). Also B-freeze runs on a slow runner: 879, 855.
- The advisor agreed; STOP line set in advance: any post-parallel p95 ≥ ~950 ms in 3 reruns → report to main (no budget change).

## Problem 2: integration job time

- Before: job 445–593 s. Runner variance is ±20% on the same code (411 s vs 566 s parallel step).
- Per-fixture setup (slow runner, 00405da/ab629c2): app 313 s (732×0.43), session_client 110 s, two_workspaces 107 s
  (195×0.55), load_fixture 66 s (3×22), db 55 s, swept_app 37 s, dbos 39 s. Setup ≈ 42% of worker time; busy ≈ 93%
  of 4 workers (little scheduling tail).
- After gc.freeze only (ab629c2): parallel 522 s, job 9:14 (slow runner). No clear gain.
- create_app (2d86610): locally 0.37 → 0.23 s per app incl. first-route materialisation. `walk_routes` gives all 108
  routes equal and the OpenAPI JSON is identical to main's (checked with a temp script, deleted). Estimate: 30–40 s wall.
- Considered and rejected: two_workspaces per-worker template (the same workspace ids would repeat across tests on a worker,
  which risks the process-global limiters/caches keyed by workspace id), cheaper argon2 (sign-in rehashes), pooling (cross-loop
  hazard), load_fixture template (3 tests on random workers).
- Sent to main (22:2x): judgement that speed-ups won't keep main + P2-04/P2-08/P1-08/P3-14 under 10 min with margin;
  recommend a decision-8 spec PR (coordinator's call, not ours).

## Exact next steps

1. Push is done at the handoff (HEAD includes 2d86610 + this file). Wait for CI on it: check T-01 diag output in the serial
   step and the `FIX setup ... app` mean (should fall ~0.43 → ~0.28 s on a slow runner).
2. Get ≥3 post-parallel T-01 samples with diag still on: `gh run rerun <run> --job <integration job id>` (per-job ids via
   `gh run view <run> --json jobs`). While a run is in progress, the job log comes from
   `gh api --allow-escape-sequences repos/SpaceshipCreative/tumnis-guide/actions/jobs/<id>/logs` (Bash `allowed_domains: ["*.blob.core.windows.net"]`).
3. Revert every diag artifact in one commit: delete `backend/tests/_diag_t01.py`, `_diag_prof.py`, `_diag_fix.py`,
   `.github/workflows/diag.yml`; restore `.github/workflows/ci.yml` integration steps EXACTLY to main
   (`/usr/bin/git diff origin/main -- .github/workflows/ci.yml` must be empty). Delete HANDOFF.md in a chore commit too.
4. `make check` (set SEMGREP_SETTINGS_FILE/LOG_FILE/VERSION_CACHE_PATH under $TMPDIR) and `make guards`.
5. ≥3 clean CI runs (rerun the integration job) for green proof and job durations. Report ≥3 samples each.
6. Write the PR body (root cause with evidence, fixes, before/after T-01 samples and job durations, docs cited: Python
   `gc.freeze` https://docs.python.org/3.13/library/gc.html#gc.freeze; pytest 9.1.1 hookspec `pytest_collection_finish`
   ("called after collection has been performed and modified; any conftest plugin can implement it"); FastAPI 0.141.1
   `include_router` (Context7 /websites/fastapi_tiangolo + the installed `fastapi/routing.py`: lazy `_IncludedRouter`, and the
   no-prefix path walks every route to validate paths). End it with a blank line + `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
   `gh pr edit 112 --body-file <file>`, then `gh pr ready 112`. Wait ~10 min for an auto review before commenting
   `@coderabbitai review` ONCE (quota tight). Run the review loop (~/tumnis-coordinator/pr-review-loop.md).
7. Send "#112 MERGE-READY at <sha>" to main. Never merge.

## Rules and gotchas learned

- Use `/usr/bin/git`, one plain command per call (no `cd … &&`, no pipes with git). A `gh` call with a jq filter
  containing `select(...)` inside a compound command is refused; put it in a script (`waitjob.sh`).
- `gh run view <id>` needs `-R SpaceshipCreative/tumnis-guide` outside the worktree.
- Branch rename to fix/ci-health worked (config write failed, harmless). Push with `/usr/bin/git push origin HEAD:fix/ci-health`.
- Never touch the T-01 test file, its budget, markers, timeout-minutes, the job set, required-checks.txt or test_ci_config.py.
