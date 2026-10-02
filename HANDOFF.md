# HANDOFF: JOURNEYS-A (J1, J6, J7 planning journeys), after continuation c1

Instructions: ~/tumnis-coordinator/prompts/wave1/JOURNEYS-A.txt (binding). Branch
`fix/journeys-planning`. Push with `/usr/bin/git push origin HEAD:fix/journeys-planning` from
a throwaway worktree branch that has merged origin/fix/journeys-planning. Never switch
branches. **PR #164 (draft)**: https://github.com/SpaceshipCreative/tumnis-guide/pull/164.
The body is in /tmp/claude-1002/JOURNEYS-A-c1/pr-body.md (cites APP-05 and the Context7 docs).
**Add decision 86 and the clock fix to it**, then `gh pr edit 164 --body-file ...`.

## Status per journey
- **J1 (A1.2): GREEN and UNMARKED.** Fix: `TodayPanel.tsx` + `SwapPicker.tsx` keep the Swap
  dialog open (options aria-disabled) until the swap settles. `lib/fetch.ts` `useWrite().mutate`
  takes per-call callbacks. Vitest: `TodayPanel.swap.test.tsx`. Marker removed in 1b708773,
  citing CI run 36966799150. Step 3 still has an inherent small race (optimistic accept vs
  J1's immediate status GET; TodayPanel.test.tsx locks the optimistic state). Main said:
  raise it again only if CI flakes.
- **J6 (A1.3): GREEN and UNMARKED (APP-05).** Fix: calendar fake scenarios
  (`calendar/adapters/fake.py` `parse_calendar_script`, registered for `calendar.google`;
  scenario `tests/recordings/google_calendar/scenarios/no_ninety_minute_gap.json`), the
  `calendar-sync` test tick (`calendar/testing.py`, imported by `calendar/router.py`), and the
  J6 helper in `frontend/e2e/phase1.ts`, which connects the two fake Google accounts through
  the real OAuth routes (`TestFakes.request` added in `e2e/testHooks.ts`). Marker removed in
  973a5d16, citing CI run 36966799150.
- **J7 (A1.6): IN PROGRESS. The clock fix is COMMITTED (929a6833, `make check` passed); J7 is still marked.** See the next steps.
  - Done, in the helper: the AI task gets its result through a real scripted run, Jev labels
    the invoice task Hybrid, and the helper waits until `enrichment_status` is `done`.
  - Clock fix (Scott/coordinator **decision 86**: in fakes mode the worker follows the test
    clock), commit 929a6833:
    - `core/fake_scripts.py`: `store_fixed_clock`, `fixed_clock_instant`, `worker_now` (row
      `core.fixed_clock` in fake_scripts; no migration; reset empties it).
    - `core/testing_routes.py` `set_clock`: stores the instant after set or advance.
    - `agents/workflows.py`: `finished_at` stamps use `await fake_scripts.worker_now()` in
      `_record_outcome` (skill and enrichment runs), `finish_run` and `fail_orphan_step`.
      Deliberately NOT changed: `started_at` (prepare_run), `now_s` (time-limit ceilings: a
      frozen clock would break timeouts), token issue and revocation, and the runner_lost
      sweep.
    - Tests (red first, now green locally): `backend/tests/integration/test_worker_test_clock.py`
      and `agents/tests/integration/test_run_end_test_clock.py`.
  - J7 steps after "prepared by agents" (Queued overnight, Rolls over +1 tonight and
    rollover-count, the phone sheet height, the Done/Escape no-writes check) have not been
    reached yet. Find out with the local diag run or CI.

## Commits on the branch (oldest first, after 484c71cf)
- 2193b782 fix(planning): the swap picker closes only once the swap is saved
- 73d6b54a feat(calendar): scriptable Google fake scenarios and the calendar-sync test tick
- 6bc1beae test(acceptance): J6 and J7 arrange helpers connect accounts and wait for enrichment
- 1b708773 test(acceptance): J1 (A1.2) passes; remove its test.fail() marker
- 973a5d16 test(acceptance): J6 (A1.3) passes; remove its test.fail() marker
- d1632e8f Merge origin/main (brings #152 agents_0011, #160 hosted keys)
- 929a6833 fix(agents): in fakes mode a run ends at the test clock's time (decision 86)
- (this handoff commit)

## CI
- Run 36966799150 (at 6bc1beae): every job green except e2e. e2e "failed" only because J1
  and J6 reported "Expected to fail, but passed" (phone), and max-failures stopped the run
  before J7. A1.5, A1.1 and J3 failed as expected (they are other groups' journeys).
- d1632e8f is pushed and CI is running on it. Expect e2e to reach J7 (still marked).
- No CodeRabbit review yet (the PR is a draft). Request one only when the PR is marked ready.

## Next steps
1. Read CI on the pushed head (`gh pr checks 164`; `gh run view <id> --log-failed`). Run
   `make check` with `SEMGREP_SETTINGS_FILE/SEMGREP_LOG_FILE/SEMGREP_VERSION_CACHE_PATH`
   set to files under $TMPDIR.
2. Rebuild the local stack, then run the J7 diag copy (decision 76) to find the next failing
   step:
   `docker compose --progress quiet -p tumnis-ja --env-file /tmp/claude-1002/JOURNEYS-A-c1/compose.env -f <worktree>/deploy/compose.test.yaml up -d --wait --build`,
   copy `/tmp/claude-1002/JOURNEYS-A-c1/diag/J7.diag.spec.ts` to `frontend/e2e/_diag/` and
   `playwright.diag.config.ts` to `frontend/`, then run
   `frontend/node_modules/.bin/playwright test -c playwright.diag.config.ts J7` with the
   sandbox off. The api is at http://localhost:18931 (use `localhost`). Remove the diag files
   before `make check` and before staging.
3. When CI shows J7 "Expected to fail, but passed", remove its marker in its own commit,
   citing the run id, and update the header comment (decision 78). Retry once if denied,
   then report READY EXCEPT MARKERS.
4. Update the PR body (decision 86, the clock fix, J7), `gh pr ready 164`,
   `gh pr comment 164 --body "@coderabbitai review"` once, then run the review loop. When CI
   is green and there are no open threads, send main "#164 MERGE-READY at <sha>".
5. Tear down the stack: `docker compose -p tumnis-ja -f <worktree>/deploy/compose.test.yaml down -v`.
   Note: `tumnis:test` is a shared image tag; this agent rebuilt it (other stacks keep their
   running containers).
6. Delete HANDOFF.md in a chore commit when done.

## Notes from c0 (still valid)
- Playwright's request context sends the `__Host-` cookies only to `localhost`, not to
  127.0.0.1.
- The diag config (in /tmp/claude-1002/JOURNEYS-A-c1/diag/) has outputDir
  /tmp/claude-1002/JOURNEYS-A-c1/test-results.

## Shared-file edits
`frontend/src/lib/fetch.ts` (additive `mutate` callbacks), `frontend/e2e/testHooks.ts`
(additive `request`), `frontend/e2e/phase1.ts` (helpers), `core/fake_scripts.py`,
`core/testing_routes.py` and `agents/workflows.py` (the clock fix, 929a6833).

## Scott items
- Decision 86 (coordinator default; Scott may override): the worker follows the test clock
  in fakes mode.
- J1 step 3 race: TodayPanel.test.tsx locks the optimistic accept. Raise it only if CI flakes.
