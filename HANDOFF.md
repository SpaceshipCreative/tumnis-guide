# HANDOFF: FIX-board-drag (continuation 0 to 1)

Branch `fix/board-drag` (pushed). **No PR opened yet.** Open it as a draft:
`gh pr create --draft --base main --head fix/board-drag --title "fix: board keyboard drag race and perf job flake" --body-file <file>`,
ending the body with a blank line and the Claude Code line. Do NOT request a full CodeRabbit review.

`.git/config` is read-only, so the local branch is the throwaway worktree branch; push with
`/usr/bin/git push origin HEAD:fix/board-drag`.

## Commits

| SHA | What |
| --- | --- |
| f83b9b12 | test(frontend): keyboard drop waits for over (red; fails on main for the right reason: onEnd called with stale over) |
| 71aa1315 | fix(frontend): SettledKeyboardSensor waits for `over` to agree with the collision under the card |
| 00326939 | test(frontend): ws burst test (red: 6 fetches for 5 messages) |
| 1da3a7c6 | fix(frontend): live burst coalescing (first version, 250 ms trailing only; superseded) |
| 14a4d461 | fix(frontend): first live message after a quiet moment is read at once, the rest per 200 ms window |
| ea4de82f, d8936556, 409199fd | merges of origin/main (#155, #161, #172) |

| 6fd38c7d + next | chore: this handoff |

`make check` passed at 409199fd.

## CI and review state

No PR yet, so there are no PR CI runs and no CodeRabbit threads. A branch push may have
started branch CI: `gh run list -R SpaceshipCreative/tumnis-guide --branch fix/board-drag`.
The local `tumnis-bd` stack is still up on port 18957, built at d8936556 (not HEAD).

## Verify commands

- `make check` at HEAD.
- From `frontend/`: `npx vitest run src/components/board/keyboard.test.ts src/lib/ws.burst.test.ts src/lib/ws.test.ts`
- `/usr/bin/git ls-remote origin refs/heads/fix/board-drag` should show the handoff SHA.
- Playwright and docker: see Next steps.

## 1. T-P0-24-05 keyboard drag (root cause found and fixed)

- Cause: `frontend/src/components/board/keyboard.ts`, `settle()` treated "snapshot unchanged
  for a frame at frame >= 3" as settled. On a phone, ArrowRight only scrolls the board
  (dnd-kit's KeyboardSensor `scrollTo` + `return`, core.esm.js ~1254). `over` then follows
  through a scroll event, a render, a passive effect (`setOver`, DndContext ~3238) and another
  render. On a busy runner nothing changed for 3 frames, so the queued Space drained with
  `over` = the Backlog card: "dropped in Backlog", no move request.
- Proof: instrumented build under 6x CPU throttle (uncommitted diag spec) showed ticks 1-3
  with unchanged snapshot while `collisions[0]` had already moved to the Today card; then
  Space ran with stale over. Repro: phone 4/8 and 4/6 failing at base.
- Fix: the sensor runs the board's collision detection (`closestCorners`, passed as the new
  `collisionDetection` sensor option from BoardView) on the live droppable rects (dnd-kit's
  `Rect` getters follow scroll) and holds keys until `over` agrees (plus the old stability
  check, 30-frame cap). It also settles after the pick-up before handling the first key.
- Evidence after the fix: throttled diag 40/40 (20 phone + 20 laptop) "dropped in Today";
  unthrottled T-P0-24-05 20/20 at base (it does not reproduce unthrottled locally).
- Still to do: one clean repeat run of the locked spec on the final build:
  `board.spec.ts --repeat-each=20` both projects (my first run was cut short by my own
  file move, all ~74 completed tests passed).

## 3. /v1/review/count storm (product fix)

- Cause: `frontend/src/lib/ws.ts` `onmessage` called `invalidateQueries` per message, whose
  refetch cancels the in-flight one and starts a new request. One quick-add makes the worker
  recompute blocking impact of the project's open review items
  (`backend/tumnis/modules/tasks/review.py` `refresh_review_impact`, one `review_item` live
  message per changed item), so the A0.1 run (e2e 36976166987, api.log) shows
  ten review counts in the second after one POST /v1/tasks and 22 in 5 s.
- Fix: every message still marks its queries stale synchronously (locked T-P0-22-09 keeps
  passing); the first message after a quiet moment refetches at once, later ones within
  200 ms are read together at the window end (`LIVE_WINDOW_MS`).
- The first version (250 ms trailing only) failed J2 A1.1 locally (label must show within
  1 s of Enter): hence 14a4d461. **The full e2e suite has not been re-run on 14a4d461.**
  Last full local run (at d8936556, old ws version): 63 passed, 4 failed:
  J2 A1.1 phone+laptop (latency, should be fixed by 14a4d461), T-P4-05-10 laptop and J8 A2.6
  laptop (unknown; check whether they fail on main locally too before blaming this branch).

## 2. performance job (evidence; no budget or measurement change made)

- #171 job 110756781396 failed on Lighthouse LAN TBT of the dashboard `/`: 218.5 ms vs 200
  (median of 3); every k6 line said ok.
- History of ~107 performance jobs (2026-10-02 05:10-09:25, logs in
  `/tmp/claude-1002/FIX-board-drag-c0/perflogs`, artifacts in `.../artifacts`, scripts
  `perfhist.py`, `lhfail.py`, `lhdist.py`):
  - k6 quick_add p95: typically 25-40 ms; ~10% of runs have a long tail
    (p90 100-180 ms, max 300-560 ms, median unchanged, typeahead unaffected); 2 runs over the
    218 ms limit (main 246.6, wp/P4-04 236.4). The same commits pass on rerun. The worker is
    labeling k6's own quick-adds and recomputing blocking impact during the run (write-path
    contention). Not a code regression. baseline.json (167.8 ms, commit 632b828) is far above
    today's typical p95.
  - Lighthouse LAN TBT: 9 failures, all but one on the dashboard (201-249 ms). Two runner
    classes: fast runners give dashboard medians 50-110 ms, slow ones 150-220 ms, and the
    three runs within one job vary by up to 130 ms (e.g. 142/157/270). The dashboard sits at
    the 200 ms budget on the slow class.
  - Possible contributor fixed here: live invalidation storms re-rendering the dashboard
    while the worker drains the load set during Lighthouse. Compare this PR's perf runs.
- Proposal to send to main (NOT done yet, budgets are spec): (a) Lighthouse numberOfRuns 5
  (median of 5) for the LAN gate if the 5-minute job budget allows (each run ~13.5 s);
  (b) for k6, wait for the worker's queue to drain after the load-set reset and report the
  tail; or a product WP to cut dashboard TBT. Ask main before touching any budget.

## Next steps

1. Rebuild the local stack at HEAD and re-run: full e2e once, `board.spec.ts
   --repeat-each=20`, and the throttled diag spec (copies parked in
   `/tmp/claude-1002/FIX-board-drag-c0/parked/`: `pw-bd.config.ts` goes in `frontend/`,
   `zz-diag-drag.spec.ts` in `frontend/e2e/`; never commit them).
   Stack: `docker compose -p tumnis-bd --env-file /tmp/claude-1002/FIX-board-drag-c0/compose.env -f <worktree>/deploy/compose.test.yaml up -d --wait --build`
   (port 18957, image tumnis:board-drag). Playwright runs in the
   `mcr.microsoft.com/playwright:v1.63.0-noble` container with `--network host`,
   `-e NODE_OPTIONS=--dns-result-order=ipv4first`, the worktree and scratch dir mounted at the
   same paths, `npx playwright test -c pw-bd.config.ts ...` (the sandbox blocks localhost, and
   npx from the root picks a different Playwright).
2. Open the draft PR (body: the three root causes above, evidence, docs cited: dnd-kit legacy
   v6 KeyboardSensor/collision docs and source of @dnd-kit/core 6.3.1; TanStack Query v5
   invalidateQueries `refetchType: 'none'` and refetchQueries).
3. Send the perf proposal to main; wait for CI; answer CodeRabbit threads; when green mark
   ready and send "#<PR> MERGE-READY at <sha>" to main.
4. Stop the stack at the end: `docker compose -p tumnis-bd ... down -v`.

## Deviations

- Red test commits were made without `make check` (red by design), as FIX-main-red did.
- Regression tests are plain Vitest tests named after the issue, not spec T-IDs.
- The handoff commits (docs only) were made with `make check` red on one test only:
  `tests/meta/test_security_job.py::test_semgrep_rules_pass_their_own_tests` failed because
  the sandbox now makes `~/.semgrep` read-only (`Errno 30` on settings and semgrep.log);
  1962 other tests passed. The same check passed at 409199fd in an earlier sandbox. Re-run
  `make check` before the next code commit; if it still fails, report it to main rather than
  working around the sandbox.

## Scott items

- Whether a 200 ms live-refetch window is acceptable product-wise (first message is still
  immediate).
- The perf proposals above (any change to Lighthouse runs or k6 measurement).
