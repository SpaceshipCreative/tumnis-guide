# HANDOFF: fix/ci-integration-budget (continuation 2 needed)

Task: bring the CI `integration` job (budget `timeout-minutes: 10`, locked by
T-P0-03-16 in `backend/tests/ci/test_ci_config.py`) comfortably under budget, aiming for
pytest under about 7 min on the GitHub runner. Never weaken a test assertion. Don't merge.
Original prompt: `~/tumnis-coordinator/prompts/ci-integration-budget.md` (binding).

**Blocked on CI billing.** Since about 22:28Z every GitHub Actions job fails without
starting ("recent account payments have failed or your spending limit needs to be
increased"). Only Scott can fix it. Don't rerun jobs or read those failures as timing data.

## State

- PR **#59** (draft, opened to get CI; CodeRabbit skips drafts):
  https://github.com/SpaceshipCreative/tumnis-guide/pull/59. Its body is a placeholder;
  replace it with the real body before `gh pr ready 59`. The coordinator's `open-pr.sh` is
  not needed now that the PR exists: mark it ready, then comment `@coderabbitai review`.
- Branch `fix/ci-integration-budget`; the continuation agent worked on local branch
  `agent/ci-budget-c1` and pushed with
  `/usr/bin/git push origin agent/ci-budget-c1:fix/ci-integration-budget`.
- origin/main (31d6554, #47 P1-09) is merged in (1166d39). #47 added about 40
  integration tests (719 items now, up from 678).
- Commits on the branch:
  - 8675aa8 `ci:` integration job gets `--dist worksteal --durations=40` (keep both).
  - 44ffe0a TEMPORARY: `--durations=0 --durations-min=0.05` (revert to `--durations=40`).
  - 362e0b3 TEMPORARY: `backend/tests/_fixture_timing.py` (per-fixture setup timing,
    on only when `TUMNIS_FIXTURE_TIMING` is set), registered in `backend/conftest.py`,
    plus the `env:` and "Fixture timing (temporary)" step in the integration job. Revert
    all of it before the PR is ready.
  - a48967b the three speed-ups (below). **Not yet measured in CI** (billing).

## Scott's decision (scott-decisions.md item 8)

If the speed-ups can't reliably keep the job under 10 minutes with margin, raising the
integration budget to 15 minutes is allowed as a spec change: change T-P0-03-16
(`backend/tests/ci/test_ci_config.py`, `"integration": 10`) and `ci.yml` in this PR,
explain it in the PR body, and say Scott must add the `spec-change` label (never add it
yourself). Speed-ups that fit under 10 minutes are preferred.

## Measurements (GitHub runner: 2 vCPU, so `-n auto` = 2 workers)

| Run | Head | Items | pytest | Job |
|---|---|---|---|---|
| 36629696440 (PR #53 baseline) | dbe079b | 730 | 581.67 s | cancelled at 10:00 |
| 36635306762 (worksteal + durations=40) | 8675aa8 | 678 | 530.22 s | 9m10s |
| 36636373780 (durations=0) | 44ffe0a | 678 | 499.47 s | 8m42s |
| 36637761934 (merged main + fixture timing) | 362e0b3 | 719 | 401.91 s | 7m02s |

Runner speed varies a lot run to run (400 to 580 s for about the same suite), so the
target has to hold on a slow runner: about 420 s on a slow runner means about 320 s on a
fast one. Job overhead outside pytest is about 20 s (uv cache hits). pytest itself spends
about 30 s before the first test (xdist workers importing and collecting everything:
"Installed" at 22:08:43, session header 22:09:14, first 10% line 22:10:15).

Worker time adds up to about 2 x wall (both workers stay busy): run 36637761934 summed
760 worker-seconds of setup+call+teardown for 402 s wall. So every 2 worker-seconds cut
is about 1 s of wall.

Per-fixture setup totals, run 36637761934 (gw0 + gw1, seconds / uses):

- `load_fixture` 92.3 / 2 (46 s per load of the 2,000-task set: one server connection
  per record under NullPool)
- `app` 63.8 / 425 (0.15 s each: FastAPI rebuilding ~190 routes in `create_app`;
  production code, left alone)
- `dbos` 60.2 / 54 (1.1 s each: DBOS 3.1.0's queue manager lists queues at launch, finds
  none because register_queues runs after launch, then waits its 1 s check interval)
- `session_client` 47.8 / 167 (0.29 s: argon2 verify with library defaults + TOTP)
- `db` 38.5 / 704 (0.05 s clone+drop each)
- `pg_container` 18.2 per worker (image pull + start; both workers at once, first test)
- `two_workspaces` 28.4 / 121, `<teardown>` 25 total, `clamd` 20.7 once, `seed` 9.3,
  `minio` 7.7, `swept_app` 10, `workspace` 7.8, `sftp_server` 3.5, `pgbouncer` 1.9.

Slowest tests (run 36637761934):
55.3 s call `test_lifecycle_pg.py::test_random_lifecycles_match_the_matrix_on_postgres`
(Hypothesis stateful, 25 examples x 30 steps, every read a new connection; 87.6 s in the
slower run); 48.1 s and 45.4 s setup of the two `test_typeahead.py` tests (load_fixture);
30.5 s setup `test_a1_5_pdf_to_packet` (clamd + minio start); about 19 s setup of each
worker's first test (pg_container). Per file: typeahead 102, a0_3 tenant isolation 79
(70 tests, 0.92 s setup each), authz matrix 59, lifecycle 55.

Raw logs and the aggregation script are in the previous agent's scratchpad
(`/tmp/claude-1002/-home-claude-Projects-Tumnis-Guide/9f3b07ec-b982-420c-89db-ecef45097e2c/scratchpad/`:
`int1.log`..`int3.log`, `dur2.txt`, `dur3.txt`, `agg.py`); they may be gone after a
reboot. Re-download with
`gh api --allow-escape-sequences repos/SpaceshipCreative/tumnis-guide/actions/jobs/<id>/logs`
(Bash `allowed_domains: ["*.blob.core.windows.net"]`).

## Speed-ups in a48967b (backend/tests/fixtures/__init__.py only)

1. `PG_TEST_SETTINGS` adds `scram_iterations=1`: the harness roles' passwords are set
   after start (bootstrap_roles), so each of the many NullPool connections skips two
   4,096-round PBKDF2 runs. Test server only, like fsync=off.
2. `_load_set` (seed, load_fixture, app_with_fakes) loads through pooled engines, then
   disposes them on the same loop and reconfigures exactly as before
   (`configure(app_url=db.app, direct_url=db.app, pooled=False)`). Expected: load_fixture
   46 s -> a fraction.
3. `dbos` fixture: `_save_queue_rows` keeps the `dbos.queues` rows of the worker's first
   launch (JSON); `_restore_queue_rows` re-inserts them after the truncate, before
   `DBOS.launch()`, so the queue manager starts the worker threads at launch (confirmed
   locally: "Listening to 3 queues" right at launch). register_queues still runs after
   launch. Expected: about 1 s off each of 54 dbos fixtures.

Local check (`make test-int`, VM load average about 50, 32 xdist workers):
706 passed, 6 failed, 1 error. All are load symptoms, not these changes: the rclone
setup error (Docker "failed to set up container networking", and the known local-only
rclone case); relay latency asserts (2.5 s and 3.3 s against 1.0 and 1.2 s); readiness
503 because the health checks timed out; typeahead p95 461 ms against 100 ms;
`test_worker_killed_between_enqueue_and_mark_sent_delivers_once` (worker too slow to
reach its kill point in 30 s). `make check` passes (set `SEMGREP_SETTINGS_FILE`,
`SEMGREP_LOG_FILE`, `SEMGREP_VERSION_CACHE_PATH` to files under the scratchpad).

## Next steps (once Scott fixes billing)

1. Push any commit (or `gh run rerun` the latest run) and read the integration job:
   pytest time, the durations list, and the "Fixture timing (temporary)" step output
   (it prints only the first 60 lines per worker; widen `head` if needed).
2. If pytest is not comfortably under about 7 min (and allowing for slow runners), more
   levers, in order of expected value:
   - `-n 3` in the integration job (tests wait on Postgres, DBOS polling and
     subprocesses; measure CPU headroom; watch the relay 1 s latency tests and typeahead
     p95 for flakes).
   - Pre-pull `pgvector/pgvector:pg18` in the background at the start of the integration
     step (overlaps the ~30 s xdist start-up; saves up to ~18 s wall).
   - Cache catalog lookups in `two_workspaces` (`tests/meta/_catalog.py`: the schema is
     the same for every clone on a worker).
   - Lifecycle test: it's a locked spec test (spec-guard locks every function that
     asserts, and `_machine` does). Only its fixtures in
     `tumnis/modules/tasks/tests/conftest.py` (`owner_rows`, `make_task`, `set_status`)
     can change; connection reuse there is the only lever.
   - Do NOT touch Hypothesis settings or assertions in test files (spec-guard fails the PR).
   - If it still can't hold with margin: Scott's item 8 (15 min budget, spec-change PR).
3. Revert the temporary measurement (commits 44ffe0a, 362e0b3: back to
   `--durations=40`; delete `backend/tests/_fixture_timing.py`, its `conftest.py` entry
   and the ci.yml `env:` and timing step). Keep `--dist worksteal --durations=40`.
4. Write the PR body (before/after timings from the table above plus the new run, the
   slowest tests before and after, the changes and why they're safe; end with a blank line
   and the Claude Code line), `gh pr edit 59 --body-file <file>`, `gh pr ready 59`,
   comment `@coderabbitai review`, and run the review loop
   (`~/tumnis-coordinator/pr-review-loop.md`). Don't merge.
5. Delete this HANDOFF.md in a `chore:` commit when done.
