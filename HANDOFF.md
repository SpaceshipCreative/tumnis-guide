# HANDOFF: SEED impl-2 (acceptance seed runtime), continuation 3

**PR #138** (https://github.com/SpaceshipCreative/tumnis-guide/pull/138), branch `wp/SEED-impl-2`.
- Push only with `/usr/bin/git push origin HEAD:wp/SEED-impl-2`.
- Scratch folders: c2 `/tmp/claude-1002/SEED-impl-2-c2/`, c3 `/tmp/claude-1002/SEED-impl-2-c3/`. Make your own (`SEED-impl-2-c4/`) for new files.
- Helpers:
  - `bash /tmp/claude-1002/SEED-impl-2-c3/check.sh`: `make check` with the semgrep env vars, for the c3 worktree path. Copy it and fix the `cd` path for your worktree.
  - `/tmp/claude-1002/SEED-impl-2-c2/wait-run.sh <run-id>`: waits for a CI run.
- PR body draft: `/tmp/claude-1002/SEED-impl-2-c2/pr-body.md`. It is NOT applied to the PR yet, and it is missing c3's work (see step 4).
- Binding rules:
  - Scott decision 51: never edit or remove a journey's `test.fail()`/xfail marker to diagnose, not even locally. Do not use `--runxfail`.
  - Coordinator notes: `~/tumnis-coordinator/agent-reports/SEED-impl-2-coordinator-notes.md`.
  - Coordinator (c3): don't touch ci.yml. Main's integration job sits at its 15-min limit, and a fix (`fix/ci-integration-speed`) is coming. If #138's `integration` is cancelled, re-run it at most once. When the coordinator says the fix merged, merge origin/main and re-run CI.
  - Coordinator (c3): the reset's lock-timeout 503 and the diagnostics run only under the test routes. `core/testing_routes.py` is mounted only with fake adapters. That holds now; keep it that way.
- Local note: a sandboxed Playwright can't reach the stack (loopback is isolated), and `npx playwright` from the repo root resolves the wrong version. Use bare `make up`, then `make e2e`, then `make down`. Error contexts land in `frontend/test-results/`.

## Commits (c3)

| SHA | What |
| --- | --- |
| 92de985 | Merged origin/wp/SEED-impl-2 into the c3 worktree (main already included). |
| e68ef3d (red), 9db0887 | T-SEED-25: a stuck reset reports its lock holders (`blocked_report`, `await_chain`, unit). T-SEED-26: a fake `ask_human` playback ends quietly once a reset removed its run (`fake_play._ask` catches NotFound, unit). Both markers removed after they passed. |
| d03ed2e (red) | T-SEED-27 (integration, `backend/tumnis/core/tests/integration/test_reset_lock_timeout.py`), red with xfail strict. |
| 5b7431e | `core/testing_routes.py`: the TRUNCATE runs with `lock_timeout` 5 s (`set_config` with a bind value). On SQLSTATE 55P03 it logs a warning with `blocked_report` (pg_stat_activity as the app role, plus the api's parked await chains) and tries again, up to `LOCK_WAIT_ATTEMPTS` = 4. After that, `ResetBlockedError` makes the route answer 503 with the report. |
| 049c4f3 (red), 2c21f16 | T-SEED-28 (Vitest, `frontend/src/components/common/AppHeader.test.tsx`): the header's review badge span has `data-testid="review-count"`, which `e2e/phase2.ts` `reviewBadgeCount` (A2.2) reads. `AppHeader.tsx` is a shared-file edit. The marker was removed after it passed. |

Check the exact SHAs with `/usr/bin/git log --oneline -10`.

## ROOT CAUSE of the e2e stall (solved)

- J1 step 4 clicks Swap, and the picker closes before the POST lands. J1 then fails its `getPlan` assertion, which is P1-11's race (see the triage in the PR body). The test ends while the swap POST is still running.
- J1's teardown reset (`page` fixture, `POST /v1/test/reset`) starts TRUNCATE while the swap is in flight.
- `planning.api.swap_item` holds the request transaction: the advisory lock, FOR UPDATE on plan_items, and AccessShare on later tables. It then calls `day_calendar`/`_ahead`, which open a second connection and read earlier tables (calendar_events, working_hours).
- That second connection queues behind the TRUNCATE's AccessExclusive on an earlier table, and the TRUNCATE waits on the swap's first connection. The two wait on each other in Python, so Postgres sees no cycle, and every later reset waited forever.
- Evidence:
  - Run 36890868588: the swap POST never got an access-log line, and autovacuum logged "lock not available".
  - Run 36894871728, with the 20 s lock timeout: the stall broke itself, A0.1 passed after a 10 s wait, and e2e was green.
  - Run 36897810125, on 2c21f16 (5 s plus retry): e2e green, J1 at 9.4 s, A0.1 at 6.7 s, no stall.
- This is not a production bug: production never TRUNCATEs. The pattern (a nested session inside a request transaction) could only deadlock against AccessExclusive operations such as migrations. Mention that to the coordinator as an FYI for P1-11. No planning change was made.

## CI state (run 36897810125 on 2c21f16)

- Every job is green except `integration`. It failed only on T-SEED-27's XPASS(strict): 1 failed, 1637 passed.
- `preview` is pending, which is expected.
- A2.2 still fails as expected in CI. The testid was the first failure point; the next one is unknown.
- In the local `make e2e` at 17:29Z, A2.2 laptop, A1.5 laptop and T-P0-29-03 failed in setup: reset returned 500 after 3 deadlocks in a row (DEADLOCK_ATTEMPTS = 3).
  - This came after the local-only P0-29 load-set perf tests, which CI excludes with `--grep-invert "@A0\.6|@P0-29"`, under heavy worker writes.
  - It looks like a pre-existing local-only issue. No "reset blocked" lines appeared, so the lock-timeout path did not fire. Mention it, but don't change DEADLOCK_ATTEMPTS without reason.
- CodeRabbit: no open threads as of c2. Re-check after the push.

## Remaining steps

1. Remove the `@pytest.mark.xfail(strict=True, reason="spec:SEED")` line from T-SEED-27 in `backend/tumnis/core/tests/integration/test_reset_lock_timeout.py`. CI run 36897810125 showed it XPASS(strict). Run check.sh, commit `test(core): T-SEED-27 passes, marker removed (XPASS in CI run 36897810125)`, and push.
2. Read CI on that push. If `integration` is cancelled at its budget, re-run it once (`gh run rerun <id> --failed`).
3. A2.2: still expected-fail after the testid. To see its next failure point, run the stack locally (`make up`, `make e2e`, read `frontend/test-results/acceptance-A2.2-*/error-context.md`, `make down`). Fix it only if the cause is harness or SEED. Otherwise put it in the triage. Never touch its marker; remove it only after CI shows an unexpected pass.
4. Update the PR body draft:
   - Add c3's work: the root cause above; T-SEED-25..28; the testing_routes lock timeout and retry (a shared core test-only file, with the 503 report); the `AppHeader.tsx` testid (shared frontend file); the `fake_play._ask` fix.
   - Fix the "core/testing_routes.py is unchanged" sentence, which is now wrong.
   - Docs: Postgres 18 `lock_timeout` (runtime-config-client), `pg_blocking_pids` (functions-info) and pg_stat_activity visibility (monitoring-stats: other roles' query columns are hidden unless pg_read_all_stats, which is why the report queries as the app role), all via Context7 `/websites/postgresql_18`. Python `asyncio.Task.get_coro` and `cr_await` for `await_chain`.
   - Apply it: `gh pr edit 138 --repo SpaceshipCreative/tumnis-guide --body-file <file>`.
5. Delete HANDOFF.md in a `chore:` commit and push.
6. When CI is green and no threads are open, merge origin/main if it moved, then send `#138 MERGE-READY at <sha>` to main.

## Decisions and deviations (cumulative)

- Phase 1 dispatches skip `FakeAgent.dispatch` and its phase 2 hook. The fake's `dispatched` event has `runner_id: None`. `recorded_reply` returns None for JSON null. `adapter_for` is unchanged.
- Seeded tasks start no enrichment (T-SEED-22, `agents/events.py`, a shared-file edit).
- J6 is split to `wp/SEED-impl-3`.
- TICK_WAIT_S is 8 s (coordinator option a). It was not the stall's cause: the tick answered in about 1 s.
- Coordinator option (c), "reset cancels build_plan/run_skill", was replaced by the fake's `runner_lost` on a removed run (T-SEED-23/24).
- `frontend/playwright.config.ts`: `globalTimeout` 7 min and `maxFailures` 2, CI only (harness, shared file). Keep both; they made the stall diagnosable.
- c3: `core/testing_routes.py` is now changed (lock timeout, retry, report; test-only routes). `frontend/src/components/common/AppHeader.tsx` gets a `data-testid` (shared file).

## Scott items

- **Unscripted fake dispatch semantics** (option b, T-SEED-20): the fake records the run and answers nothing. Should it refuse unscripted skills at once instead? That would rewrite T-SEED-20.
  - A silent run now ends only when a reset removes it. Within a test, J7 and J8 still wait out the 90 s run timeout on the one-at-a-time maintenance queue unless they script `tumnis-master/plan`.
- FYI for P1-11: `swap_item` (and the other plan writes that call `day_calendar`) open a second connection inside the request transaction. That is harmless in production, but it can deadlock unseen against any AccessExclusive operation (here the test reset).

## Verify

```bash
cd backend && uv run pytest -q -n 3 -m "not integration and not contract" tumnis/core/tests/unit/test_reset_blocked_report.py tumnis/modules/agents/tests/unit/
cd frontend && npx vitest run src/components/common/AppHeader.test.tsx
bash /tmp/claude-1002/SEED-impl-2-c3/check.sh
gh pr checks 138 --repo SpaceshipCreative/tumnis-guide
```
