# HANDOFF: fix/vitest-load-flakes (PR #79, issue #76)

- PR: https://github.com/SpaceshipCreative/tumnis-guide/pull/79 (label `bug`, body says `Fixes #76`). Do NOT merge; the coordinator merges.
- Issue: https://github.com/SpaceshipCreative/tumnis-guide/issues/76 (filed by this agent, AGENTS.md rule 12).
- Branch: `fix/vitest-load-flakes`. Push with `/usr/bin/git push origin HEAD:fix/vitest-load-flakes`.
- Scratch folder: `/tmp/claude-1002/vitest-load-flakes/`. It holds the logs, `pr-body.md`, the loop/burn scripts, `mc.sh` (make check with SEMGREP_* vars set) and `final.sh`.

## Commits (oldest first)
| SHA | What |
| --- | --- |
| ffffcff | test: harness.test.ts "issue #76 findBy waits out an answer slower than one second". Red on main, since the default asyncUtilTimeout is 1 s. |
| f345607 | fix: setup.ts sets asyncUtilTimeout 15 s; vite.config.ts sets `autoCodeSplitting: !process.env.VITEST`; vitest.config.ts sets testTimeout 60 s; Makefile passes `--maxWorkers=$(VITEST_MAX_WORKERS)`, which defaults to min(nproc, 8). |
| 63f09e0 | test: render.test.tsx "issue #76 a route load still running when its test ends finishes before teardown". It uses onTestFinished to check router status is idle. Red on main (`pending`). |
| f0ff37a | fix: new src/test/routers.ts (trackRouter/settleRouters); setup.ts afterEach runs `await settleRouters()` before resetHandlers and cleanup; render.tsx renderRoute sets `router.update({...router.options, defaultPendingMinMs: 0})`, and a test's own `unmount()` puts back router-core's own `startTransition` (commit without React, as in router-core/dist/esm/router.js:102); vitest.config.ts sets hookTimeout 30 s. |
| d847f8f | merge origin/main (5ad1057) into the branch. This fixes the spec-guard failure, which was a false positive from a stale base: main had meanwhile merged #72, which drops the J2.spec.ts board_rank line. |
| (next) | chore: this HANDOFF.md |

## Root causes (all verified)
1. Testing Library's asyncUtilTimeout defaults to 1 s of wall-clock time. Starved workers miss it.
2. TanStack `autoCodeSplitting` also applies under Vitest, so lazy route chunks were transformed and loaded inside tests. The heavy route tests took about 2x longer: RightRail 20.2 s split vs 10.9 s not split, TaskDrawer 19.7 vs 8.3, routes T-P0-22-01 9.1 vs 1.5.
3. The config gave 31 Vitest workers on this 32-core shared VM, and make check did not cap them.
4. Route loads outlived their tests. Routes with `pendingMs: 0` (the dashboard and the project page) hold their pending state for `defaultPendingMinMs` (500 ms of real time). A load that committed after jsdom teardown threw `ReferenceError: window is not defined` from Transitioner (routes.test.tsx), which made make check exit 1. This failed make check 1 time in 3 before f0ff37a. Instrumented runs found leaking routers in T-P0-22-01, T-P0-22-04 and ProjectPage T-P0-24-07. After f0ff37a, one instrumented full run showed 0 leaks.

## Evidence so far
- main, ProjectList.test.tsx alone: 10 of 10 runs failed at load average 47-81.
- main, full suite: 6 of 6 runs failed at load average 22-90, with 11 to 35 failing tests per run.
- After f345607 (before the router settle), full suite: 6 of 6 passed at 8 workers with 32 extra busy loops (load average 24-71), and 4 of 4 passed at 31 workers with 32 busy loops (load average 36-89). make check: 2 of 3 passed; the failure was the root cause 4 leak.
- After f0ff37a, before the merge: make check (mc2) passed 2 of 2 (load average 15 and 44). A third run was stopped so that main could be merged in first.
- CI on f0ff37a: all green except spec-guard, which was the stale-base false positive; d847f8f fixes it. The `preview` check is expected to stay pending.
- CodeRabbit: two reviews, both "No actionable comments", with 0 inline comments and 0 threads. It has not reviewed d847f8f yet.

## Remaining steps
1. Run `make check` on the merged code and confirm it passes. It was not run after the merge (d847f8f) because the handoff was requested. Then run `bash /tmp/claude-1002/vitest-load-flakes/final.sh`: it runs make check 4 times, then the full suite at 8 workers with 32 burn loops ×4, at 31 workers with burn ×3, and at 8 workers without burn ×3. Summaries are in `/tmp/claude-1002/vitest-load-flakes/{mc2,final-*}/summary.txt`. Delete or rename the old `mc2` directory first, or its runs will be appended to. Grep the logs for `Hook timed out`, `is still loading`, `window is not defined` and `Errors`.
2. Update the PR body (`gh pr edit 79 --body-file /tmp/claude-1002/vitest-load-flakes/pr-body.md`) after editing it to add:
   - root cause 4, with its symptom and the LEAK findings;
   - routers.ts and the settle step in afterEach;
   - `defaultPendingMinMs: 0`, citing the TanStack RouteOptionsType docs (`pendingMinMs` defaults to `routerOptions.defaultPendingMinMs`, which is 500) and noting that `update` is on the RouterCore type;
   - the unmount `startTransition` override, citing router-core `router.js:102`, which is router-core's own pre-mount implementation;
   - hookTimeout 30 s;
   - the second red test;
   - the new make check rows.
   It should also state one remaining case: a router the test itself unmounted while its load was already inside the Transitioner's startTransition stays pending forever. That is harmless, because it touched React while window still existed.
3. Comment `@coderabbitai review`, then follow ~/tumnis-coordinator/pr-review-loop.md until there are 0 unresolved threads and CI is green.

## Decisions and deviations
- Test bodies are unchanged (spec-guard). The fixes are in the harness, config and Makefile only.
- The Makefile cap uses the documented CLI flag `--maxWorkers`. Vitest 5.0.2 also reads a `VITEST_MAX_WORKERS` env var, but that is in source only.
- No test uses both `vi.useFakeTimers()` (fully) and a router render, so settleRouters's `waitFor` polling is safe.

## Scott items
- T-P0-22-08 (src/lib/optimistic.test.tsx) races the clock. The optimistic "B" is visible only during MSW's `delay(50)` in real time, and it failed once under load (the DOM already showed "C"). A deterministic fix is to gate the 409 on the test (like Composer's `createGate`). That changes the test body, so it is a spec change and needs Scott.
- Some tests don't register every handler the page requests (for example ProjectList's "New project" leaves /brief and /recurrence unhandled). MSW only logs these to stderr. Fixing them means editing test bodies.

## Verify
```
cd frontend && npx vitest --run src/test/          # harness + both red-proof tests pass
cd frontend && npx vitest --run --maxWorkers=8     # full suite
make check                                          # with SEMGREP_* under /tmp/claude-1002/vitest-load-flakes/semgrep/
```
