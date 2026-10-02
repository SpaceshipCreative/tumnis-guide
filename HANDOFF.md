# HANDOFF: FIX-relay-drain (T-P0-07-05 drain stall)

- **PR:** #178, https://github.com/SpaceshipCreative/tumnis-guide/pull/178. It is ready for review (no longer a draft).
- **Branch:** `fix/relay-drain`. Push with `/usr/bin/git push origin HEAD:fix/relay-drain`. The worktree's own branch is a throwaway; the `.git/config` is read-only.
- **Head before this file:** ad197c87.

## Root cause (done)

`tumnis/core/db.py` kept one pooled AsyncEngine per role, and the worker's two event loops shared it: its own `_serve` loop (relay, folder and vault watchers) and DBOS's background loop (workflows). The watcher's first query and a workflow step's first query raced SQLAlchemy's first-connect asyncio mutex. The other thread woke the future, and the waiting loop never woke.

- **CI evidence** (run 36997230716): the step `resolve_dead_letter_if_any` was "Running", every thread was idle, and DBOS's loop sat in select.
- **Local evidence:** 6/100 runs failed. With PYTHONASYNCIODEBUG=1, 11/100 failed with "Non-thread-safe operation invoked on an event loop other than the current one".
- **After the fix:** 100/100 pass, and 100/100 with asyncio debug.

## Commits

| SHA | What |
| --- | --- |
| 27dc8a6c | `test(core)`: red tests (strict xfail) for per-loop engines and the two-loop first-connect race |
| f27c8486 | `fix(core)`: one engine per (running loop, role); markers removed |
| e6b12037 | `fix(core)`: `db.limit_pools`; the worker's own loop is capped at 2+2; `dispose()` closes only the running loop's engines |
| ad197c87 | `fix(core)`: an engine built outside any loop uses NullPool (CodeRabbit finding) |
| several | merges of origin/main, the latest being 2582ec1c |

## Review and CI state

- **CodeRabbit:** 1 thread, which was fixed in ad197c87, replied to and resolved. `@coderabbitai review` was requested again after ad197c87.
- **CI at fe8ad87a:**
  - every job passed except `unit`, which was **cancelled** (not failed; the run was superseded) and must be confirmed on the new head;
  - `preview` was pending.
- **CI at e82f3671:** `integration-b` failed once on `test_label_latency.py::test_label_p95_under_1s_at_recorded_latency`, p95 1022 ms against a 1000 ms limit. It is a timing test, and it passed on the next head. Treat it as a flake that gets one re-run.
- **CI on ad197c87:** not yet seen.

## Remaining steps

1. Wait for CI on the head after this file (`gh pr checks 178`). Known flakes get one re-run.
2. Wait for CodeRabbit's re-review. Fix or answer any new thread, then resolve it.
3. Before each push, merge origin/main and run `make check`. Run `make check` through a script that sets `SEMGREP_SETTINGS_FILE`, `SEMGREP_LOG_FILE` and `SEMGREP_VERSION_CACHE_PATH` under `$TMPDIR/FIX-relay-drain-c0/` (see `check.sh` there).
4. When CI is green and no threads are open, send "#178 MERGE-READY at <sha>" to main.

## Decisions and deviations

- **DBOS pools left as they were:** the coordinator ruled not to cut the DBOS-loop pools. The worst-case connection budget goes from 147 to 155 (the cap holds the increase to +8). The PR body states this.
- **No dispose across loops:** each loop disposes only its own engines. A version that disposed every live loop's engines leaked connections in the race test, so it was reverted.
- **Diagnostics stayed local:** the diagnostic harness edits (PYTHONASYNCIODEBUG, a stall report in `run_until_killed`) were never committed. They are saved in `$TMPDIR/FIX-relay-drain-c0/diag.patch`.

## Scott items

- **Connection budget:** the worst case is already over Postgres `max_connections=100` (147 before this PR, 155 after). Decide on smaller DBOS-loop and DBOS system pools, or a higher `max_connections`.

## Verify

- `cd backend && uv run pytest -q tumnis/core/tests/unit/test_db_engines.py`
- **Integration (Docker):** `tumnis/core/tests/integration/test_two_loops_first_connect.py` and `test_relay.py`.
- **Flake loop (diagnostic):** `$TMPDIR/FIX-relay-drain-c0/repro.sh 100 8 <log>`, which uses pytest-repeat through `uv run --with`.
