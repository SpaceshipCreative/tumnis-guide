# HANDOFF: P0-25 Quick-add and offline queue

Branch: pushed as `wp/P0-25` (local branch in worktree `agent-a02b4e70c74a3f8ca` is also named
`wp/P0-25`; push with `/usr/bin/git push origin HEAD:wp/P0-25`). **No PR opened yet.**
Frontend-only WP: no backend change, no alembic revision.

## Commits (on top of main d92412b)

| SHA | What |
| --- | --- |
| a7a664f | `test(frontend): P0-25 spec tests (red)`: T-P0-25-01..14 as expected failures, stubs, deps |
| 5dd315a | `feat(frontend): offlineQueue machine, IndexedDB store and queue sender` (T-05..10, T-14 green) |
| 707e8a0 | `feat(frontend): quick add, search palette, pending marks, composer on the queue` (T-01..04, T-11 green) |
| (this)  | `chore: P0-25 handoff` |

## Test state

- Vitest: all green, markers removed for T-P0-25-01..11 and 14 (full suite 49 files / 90 tests
  passed locally). Extra non-spec tests: remaining machine transitions (`offlineQueue.test.ts`
  bottom) and `src/lib/queueSender.test.ts` (HTTP outcome mapping).
- Playwright: `frontend/e2e/quickadd.spec.ts` T-P0-25-12 and T-P0-25-13 still carry
  `test.fail()`. **Not run locally yet** (`make up` collided with another agent's
  `tumnis-test` compose project: "No such container"; nothing of mine is left running).
- Acceptance A0.2 (`e2e/acceptance/A0.2-offline-capture.spec.ts`) and A0.1
  (`e2e/journeys/J2.spec.ts`, first test only; A1.1 stays marked) still have `test.fail()`.
  The plan says the A0.1 marker comes off in P0-25 or P0-23, whichever merges last (P0-23 is
  merged), and A0.2 turns green with P0-25.
- `make check`: frontend typecheck/lint/prettier clean; backend part passed earlier (needs
  `SEMGREP_SETTINGS_FILE=/tmp/claude-1002/semgrep-settings.yml SEMGREP_LOG_FILE=/tmp/claude-1002/semgrep.log SEMGREP_VERSION_CACHE_PATH=/tmp/claude-1002/semgrep-version`
  prefix because ~/.semgrep is read-only). `ProjectList.test.tsx` (P0-17) flaked once under
  load (findByRole 1 s timeout); passes alone 3/3. Pre-existing, not touched.
- Bundle: 173.7 KB gzipped initial JS (budget 200 KB).

## Exact next steps

1. Remove `test.fail()` from T-P0-25-12, T-P0-25-13, A0.2 and A0.1 (the `A0.1 sign in, create
   project...` test only). Verify with `make up` + `npx playwright test -c frontend/playwright.config.ts quickadd A0.2 J2`
   when port 8080 / the `tumnis-test` project is free, else let CI's `e2e` job decide (a
   marked test that passes fails CI as "expected to fail").
2. `make check` (with the semgrep env prefix), `make test`, `make test-int` (backend untouched;
   should be green as on main; `test_rclone_copy_keeps_files_removed_at_source` times out
   locally only).
3. Open the PR (`gh pr create --base main --head wp/P0-25 --title "[P0-25] impl: Quick-add and offline queue" --body-file ...`),
   then `gh pr comment <url> --body "@coderabbitai review"`, then the review loop
   (~/tumnis-coordinator/pr-review-loop.md). Known e2e flake: `POST /v1/test/reset` 500
   (issue #56, PR #64): rerun the failed job once.

## Design notes and deviations (for the PR body)

- Shared-file edits: `frontend/package.json` + lock: xstate 5.33.2, @xstate/react 6.1.0,
  idb 8.0.3 (devDependencies, exact pins, like every other frontend dep).
- `sendQueued` also deletes the item from IndexedDB inside the Web Lock on success (the plan
  only has `forgetHead` after the lock is released). Without it a second tab can pass the `has`
  check before the first tab's delete lands and send twice; T-P0-25-10 catches exactly that.
- `sendQueued` gets the QueryClient and an `onCreated` hook (undo "Task added") through
  `setSenderHooks`, set by `QuickAddHost` (main.tsx keeps its QueryClient local).
- `lib/idb.ts` falls back to an in-memory map when `openDB` rejects; `persistent()` drives
  "Offline capture is off in this browser mode" in the dialog.
- Mod+K works from inside a text field; `/` does not (types a slash). Neither fires during IME
  composition.
- The typeahead lists server hits (`GET /v1/typeahead/projects`, 150 ms debounce) followed by
  projects already in the Query cache whose name contains the text: capture offline (A0.2 types
  in the typeahead while offline) and a project created seconds ago (A0.1) still find it.
- The phone's Quick add button moved from DashboardPage to the shell (every route; A0.2 and
  T-12 tap it on project and settings pages). File moved to `components/quickadd/QuickAddFab.tsx`.
- `useCreateTask` (P0-24 composer mutation) removed; the composer enqueues. Pending rows are
  `pending:<key>` TaskLite rows in Up next with `PendingMark`; when the project cannot load
  (offline reload) a "Waiting to sync" list shows them instead.
- `sessionProbeOptions` gets `networkMode: "always"` (it already treats a network failure as
  signed in; without this the root route would hang when the query is paused offline).
- On `online` the host also invalidates queries in error state (reads that failed offline).
- T-P0-25-14 checks both directions: every name the config uses is declared in setup, and
  setup declares nothing unused (needed for the stub to be red; worth a line for Scott).
- `performance.mark("tumnis:quickadd-ready")` on dialog open (A0.6 / P0-29 reads it).
- T-P0-25-13 runs with `serviceWorkers: "block"` so `page.route` sees the POST.

## Scott items

- None blocking. Mention the both-directions R-38 sweep in T-P0-25-14.
- "Preview checked on the phone with airplane mode on and off" (done checklist) needs a human.
