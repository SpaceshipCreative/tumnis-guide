# HANDOFF: FIX-main-red (continuation 0 to 1)

PR: #171 (draft), https://github.com/SpaceshipCreative/tumnis-guide/pull/171, branch `fix/main-red`.
Push with `/usr/bin/git push origin HEAD:fix/main-red` from a worktree whose HEAD has these commits. `.git/config` is read-only, so the local branch is the throwaway worktree branch. Base is main ddb94871.

## Commits

| SHA | What |
| --- | --- |
| 488875f5 | test(core): rate limit refills on real time while the test clock is fixed (red, unit) |
| e289b620 | test(integrations): a finished connect keeps no oauth_pending row (red, integration) |
| 8b6730de | fix(core): rate limits read real time on a fakes stack |
| f09f9063 | fix(integrations): a finished connect deletes its oauth_pending row (coordinator default decision 92) |

## Findings

Main's red is several independent intermittent failures, not one conflict between #156 and #167.

1. **e2e A0.1 [laptop] (main run 36976166987): fixed in 8b6730de.**
   - The dashboard showed "Today's tasks could not be loaded" because of 429s.
   - 93 session requests arrived in about 4.9 s, against the default bucket of burst 50 at 10/s.
   - The limiter read the OverridableClock, which the Playwright follower moves in one-second steps.
   - `RateLimiter` now uses `clock.base` when given an OverridableClock (`backend/tumnis/core/ratelimit.py`).
   - Replaying the run's log with real-time refill (scratch `sim.py`) gives 0 refusals, but a minimum margin of only 6 tokens.
   - Scott item: 18 `GET /v1/review/count` calls in 5 s suggest live-invalidation fan-out, and a full page load costs 15 to 19 requests against burst 50.
2. **T-P3-02-03 `b"c1"` (fix/docling-ci 36977048918, #158 36979923301): fixed in f09f9063.**
   - The literal `b"c1"` sits in the locked assert at `test_connect_oauth.py:93`, and that test is unchanged.
   - `complete_oauth` now hard-deletes the `oauth_pending` row on success, so the loop has no rows to check.
   - The coordinator accepted this as default decision 92.
3. **T-P0-07-05 `tumnis/core/tests/integration/test_relay.py::test_worker_killed_after_handler_step_does_not_rerun_it`: NOT fixed, root cause unknown.**
   - It failed on main 36976166987 and again on #171's run 36981481795 (job 110756781237), so it now looks frequent.
   - Symptom: the restarted worker logs "Recovering 1 workflows from application version worker-killer", lists 11 queues, then logs nothing for 30 s. The `deliver_event` workflow never reaches SUCCESS.
   - Both workers use executor ID `local`.
   - Also logged: "Duplicate registration of function 'list_workspaces'" (coolify, tasks and core audit_workflows each define `list_workspaces`). Check whether it matters.
   - Lines to pursue: what the recovered workflow is waiting on, whether DBOS 3.1.0 re-enqueues a recovered queued workflow (ENQUEUED) and the events queue dequeues it, and whether #156's new `sync` queue or scheduled ticks starve or block the events queue at startup.
   - Get the subprocess worker's full log; `log_tail` only shows 60 lines.
   - Read DBOS recovery docs in Context7 before changing anything.
4. **e2e T-P0-24-05 board keyboard drag: NOT fixed, the most frequent e2e failure.**
   - It failed on main 0973da9 ([phone], 36974649831), main ddb9487 ([phone], 36978333563) and fix/journeys-run-focus ([laptop]).
   - The live region says "Write logo usage rules was dropped in Backlog", so ArrowRight didn't reach Today.
   - Relevant code: `frontend/src/components/board/keyboard.ts` (SettledKeyboardSensor) and `BoardView.tsx`. The phone board is a horizontal snap scroller.
   - Trace unpacked at `$TMPDIR/FIX-main-red-c0/tr1/` (`2-trace.trace` holds the test's actions, not yet read).
   - Check whether a live `/ws` refetch re-renders the board mid-drag, and the settle logic around scroll.
   - Read the dnd-kit KeyboardSensor docs in Context7 first.
5. **main ddb9487 "reset 500" lead: false.**
   - Run 36978333563 has no 5xx at all: 74 resets answered 204 and 6 answered 409.
   - J2 A1.1, J3 A2.1 and J8 A2.6 are marked `test.fail()`, so failing is expected.
6. **performance job.**
   - Main 36978333563 failed on quick_add p95 246.6 ms against a 218 ms limit.
   - #171's run 36981481795 also failed, but every k6 metric printed "ok"; read the job 110756781396 log for the real cause.

## CI on #171 (run 36981481795)

- Green: unit, contract, integration-a, e2e, lint, security, spec-guard, red-proof, traceability, skills, daemon, version-skew.
- Red: integration-b (T-P0-07-05) and performance (see item 6).
- preview was pending.

## Next steps

1. Find the root cause of T-P0-07-05 and fix it, test first. Then T-P0-24-05.
2. Run `make check` before each commit; semgrep needs the `SEMGREP_*` variables pointed at `$TMPDIR` files. Push.
3. Mark the PR ready when CI is green, and don't request a full CodeRabbit review. Then send "#171 MERGE-READY at <sha>" to main.

## Deviations

- Playwright stays at `workers: 1` because of the shared stack and TOTP replay.
- `make test-int` runs `-n auto` capped at 8, because the bare-command rule doesn't allow `-n 3`.
- The local integration run was polluted: the branch was fast-forwarded while it ran.
- The red-test commits were made without `make check`, since they're red by design.

## Verify

```
cd backend && uv run pytest -q -m "not integration and not contract" tumnis/core/tests/unit/test_write_conventions.py
make check
gh pr checks 171
```
