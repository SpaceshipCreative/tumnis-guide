# HANDOFF: SEED impl-2 (acceptance seed runtime), continuation 2

**PR #138** (https://github.com/SpaceshipCreative/tumnis-guide/pull/138), branch `wp/SEED-impl-2`.
- Push only with `/usr/bin/git push origin HEAD:wp/SEED-impl-2`.
- Scratch folder from c2: `/tmp/claude-1002/SEED-impl-2-c2/`. Use your own folder (e.g. `SEED-impl-2-c3/`) for new files.
- Helpers in the c2 folder:
  - `check.sh`: `make check` with the semgrep env vars.
  - `wait-sha.sh <sha>` and `wait-run.sh <run-id>`: CI waits.
  - `logline.py`: prints a JSON log line from the stack logs.
- PR body draft: `/tmp/claude-1002/SEED-impl-2-c2/pr-body.md`. It is NOT yet applied to the PR (see step 4).
- Binding rules:
  - Scott decision 51: never edit or remove a journey's `test.fail()`/xfail marker to diagnose, not even locally. Do not use `--runxfail` either.
  - The coordinator notes in `~/tumnis-coordinator/agent-reports/SEED-impl-2-coordinator-notes.md`.

## Commits (c2)

| SHA | What |
| --- | --- |
| 916b9b4, 0bb86af | Merged origin/main (#137) and removed the c1 HANDOFF.md |
| 3a95906, 1f51f60 | First T-SEED-23 attempt: the reset cancelled PENDING/ENQUEUED DBOS workflows, and TICK_WAIT_S went 25 s -> 8 s. The cancel did NOT work. In DBOS 3.1.0, `worker_concurrency` counts the in-memory running count, and Python `recv` never checks for cancellation, so the slot stayed taken. |
| 6132ca9 | Reverted the reset cancel (`core/testing_routes.py` and `core/deadletter.py` are unchanged vs main). Rewrote T-SEED-23 (this PR's own, unmerged) to "a silent fake run ends when a reset removes it" (red). |
| ae0dbe6 | `fake_play._watch_silent`: a silent (unscripted) fake run polls its `runs` row every 0.5 s. Once the row is gone (a reset), it sends `runner_lost` to the waiting `run_skill`, so the build ends within seconds. |
| e0a79eb | T-SEED-23 marker removed. It was XPASS in CI run 36882152485. |
| 45c35b5 | Merged origin/main (#135, #141). `make check` passes. |
| c5ce2fa | T-SEED-24 red: a late scripted answer ends a run that a reset removed. |
| dc31b91 | `dispatch_skill` reads the run's workflow id once. `_answer` sends `runner_lost` if a reset removed the run. Both paths share `_runner_lost`. Playwright `globalTimeout` is 7 min on CI. |
| 97ccf2f | T-SEED-24 marker removed (XPASS in CI run 36888304158). Playwright `maxFailures: 2` on CI. |

## CI state (last full run: 36888304158 on dc31b91)

- Every job is green except these:
  - `integration` failed only on T-SEED-24's XPASS(strict). That is fixed in 97ccf2f.
  - `e2e` was cancelled at the job's 10-minute budget (see the open problem below).
- 97ccf2f was pushed together with this handoff. Read its run next.
- The earlier integration failures did not recur on dc31b91, so they look like flakes:
  - `test_archive.py::test_purge_is_session_only_and_audited` (UniqueViolation on `ux_agent_profiles_one_project_agent`)
  - `test_taint_propagation.py` (Hypothesis FlakyFailure)
- CodeRabbit: no open threads.

## OPEN PROBLEM: the e2e stack stalls after a slow J1 (blocks MERGE-READY)

- It happened in 3 of 3 runs since TICK_WAIT_S = 8 s (36878985292, 36882152485, 36888304158).
- What the stall looks like:
  1. J1 (A1.2, phone or laptop) takes about 13.8 s instead of about 5.7 s. Most likely its tick returns at 8 s with the build still running, and J1 then fails `toHaveCount(4)` at 5 s.
  2. The next test, A0.1 (a normal test that must pass), fails at 30 s.
  3. Every later test hits its 2-minute timeout, which looks like a hung `POST /v1/test/reset`. A reset stuck in TRUNCATE holds the reset lock.
  4. The job is cancelled at 10 minutes. `if: failure()` steps don't run on a cancel, so CI uploads no stack logs.
- Run 0bb86af (TICK_WAIT_S = 25 s) did NOT stall: J1 took 5.7 s and the e2e job passed.
- Local `make e2e` (stack via `make up`) did NOT reproduce it: J1 took 5.6 s and 6.0 s, and there was no stall.
- A local repro also did not hang the reset: tick with a master script `delay_ms` 20000, the tick returns at 8 s, then reset `set=seed` took 5.6 s. It did show that a late scripted answer was dropped, which T-SEED-24 now fixes.
- Suspects (unconfirmed):
  - (1) TICK_WAIT_S = 8 is causal somehow. Try 25 s again, or 9.5 s, under the hooks' 10 s request timeout. Note the coordinator asked for 8 s.
  - (2) The worker's real-time scheduled `planner_tick` (`*/5 * * * *`, MAINTENANCE_QUEUE, `worker_concurrency=1`) enqueues a real-date build for the fake-served acceptance master, which holds the queue.
  - (3) A worker transaction left idle across an await while TRUNCATE waits.
- 97ccf2f adds `maxFailures: 2` on CI. A stall should now FAIL fast with stack logs (e2e-logs artifact: `stack-logs/api.log`, `worker.log`, `postgres.log`, plus Playwright error-context for J1).
  - Download with: `gh run download <run> -n e2e-logs -D <dir>` and `allowed_domains: ["*.blob.core.windows.net"]`.
  - Look for reset/TRUNCATE lock waits, the J1 failure point, and build_plan/run_skill timing around J1.

## Remaining steps

1. Read CI for 97ccf2f. If e2e stalls, it should now fail with logs. Diagnose and fix (see above).
2. When e2e passes, look for any journey that unexpectedly passes. Remove its marker only after CI shows it passing.
3. Reconsider whether `globalTimeout` and `maxFailures` stay. They are harness edits that are fine to keep, and they are listed as shared-file edits in the PR body draft.
4. Apply the PR body:
   - `gh pr edit 138 --repo SpaceshipCreative/tumnis-guide --body-file /tmp/claude-1002/SEED-impl-2-c2/pr-body.md`
   - First add the stall root cause, plus `maxFailures` in the playwright.config.ts shared-file note.
5. Delete this HANDOFF.md in a `chore:` commit and push.
6. When CI is green and no threads are open, merge origin/main again if it moved, then send `#138 MERGE-READY at <sha>` to main.

## Decisions and deviations (cumulative)

- Phase 1 dispatches skip `FakeAgent.dispatch` and its phase 2 hook. The fake's `dispatched` event has `runner_id: None`. `recorded_reply` returns None for JSON null. `adapter_for` is unchanged.
- Seeded tasks start no enrichment (T-SEED-22, `agents/events.py`, a shared-file edit).
- J6 is split to `wp/SEED-impl-3`.
- TICK_WAIT_S is 8 s (coordinator option a).
- Coordinator option (c), "reset cancels build_plan/run_skill", was replaced by the fake's `runner_lost` on a removed run (T-SEED-23/24), because DBOS 3.1.0 cancel doesn't free a `worker_concurrency` slot. Already reported to main.
- `frontend/playwright.config.ts`: `globalTimeout` 7 min and `maxFailures` 2, CI only (harness, shared file).
- T-SEED-23 was rewritten before merge (its first red version asserted CANCELLED).

## Scott items

- **Unscripted fake dispatch semantics** (option b, T-SEED-20): the fake records the run and answers nothing. Should it refuse unscripted skills at once instead? That would rewrite T-SEED-20.
  - With the fix, a silent run now ends only when a reset removes it. Within a test, J7 and J8 still wait out the 90 s run timeout on the one-at-a-time maintenance queue unless they script `tumnis-master/plan`.

## Verify

```bash
cd backend && uv run pytest -q -n 3 -m "not integration and not contract" tumnis/modules/agents/tests/unit/
bash /tmp/claude-1002/SEED-impl-2-c2/check.sh
gh pr checks 138 --repo SpaceshipCreative/tumnis-guide
```
