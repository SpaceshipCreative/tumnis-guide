# HANDOFF: P0-25 Quick-add and offline queue (continuation 1 -> 2)

PR: https://github.com/SpaceshipCreative/tumnis-guide/pull/65 (`wp/P0-25` -> main, ready, not draft).
Push with `/usr/bin/git push origin HEAD:wp/P0-25` (never force). Frontend-only WP: no backend, no alembic.
Setup in a fresh worktree: `/usr/bin/git fetch origin`, `/usr/bin/git merge origin/wp/P0-25`, `npm ci --prefix frontend`,
`cd backend && uv sync --frozen --all-extras`.

## Commits on wp/P0-25 (after main 6b85801)

| SHA | What |
| --- | --- |
| a7a664f, 5dd315a, 707e8a0 | continuation 0: spec tests, machine/store/sender, UI (see earlier notes below) |
| a557050 | earlier handoff |
| b2d8c5e | merge of origin/main into the branch |
| 38a5d22 | removed test.fail() from T-12, T-13, A0.2, A0.1 |
| 454bac5 | removed the old HANDOFF.md |
| 088d3d0 | restored A0.1's test.fail() (J2.spec.ts is byte-identical to main again) |
| df01780 | fix(frontend): e2e and offline paths (fixtures, early hotkeys, known projects, loaders) |
| (next commit, "fix(frontend): queue edit and load races") | machine fixes for two CodeRabbit findings + 2 tests; then this handoff |

## State

- Local full `make e2e` (own compose project: `docker compose -p p025 -f deploy/compose.test.yaml up -d --wait --build`, then
  bare `make e2e`, then `docker compose -p p025 -f deploy/compose.test.yaml down -v`; each bare, nothing added) passed:
  T-P0-25-12, T-13 and A0.2 on phone and laptop; all other failures were expected-fail markers. Stack is torn down.
  Sandboxed shells cannot reach localhost:8080; only the bare docker/make commands can. A temporary `testMatch` in
  `frontend/playwright.config.ts` narrows runs (revert with `/usr/bin/git checkout origin/main -- frontend/playwright.config.ts`).
- First CI run (on 454bac5) failed e2e for real reasons (fixed in df01780). CI has NOT run on df01780 or later yet:
  check `gh pr checks 65`. The `preview` job was pending at the time. If e2e fails only on `POST /v1/test/reset` 500
  (issue #56, PR #64), rerun once.
- Vitest: passes with `npx vitest run --maxWorkers=3`. A default-concurrency `make check` on this loaded VM (load 30-45)
  flakes P0-17/P0-24 route tests (findBy 1 s and 20 s timeouts; different ones each run, all pass alone). Not a regression.
  `make check` otherwise green (ruff, pytest 1020 + 17, lint, prettier, tsc).
- Bundle was 173.7 KB gz before the fixes; `index` chunk grew ~0.5 KB gz.

## CodeRabbit (review on 454bac5, 4 inline comments; new `@coderabbitai review` was requested after df01780)

All four are valid; none replied to or resolved yet (no thread has been touched):
1. `machines/offlineQueue.ts` EDIT race (put after delete): FIXED in the next commit (put first, delete after put; test
   "an edited item is in IndexedDB before its send starts", was red).
2. `machines/offlineQueue.ts` `setItems` drops captures made while `loading`: FIXED (merge by key, sort by createdAt; test
   "a capture made while the queue loads survives a load that missed it", was red).
3. `components/common/SearchPalette.tsx` line ~80: Tab is not contained in the aria-modal dialog. TODO: add a shared
   `lib/focusTrap.ts` (wrap Tab / Shift+Tab between first and last focusable in the dialog) and use it in BOTH
   `SearchPalette` and `QuickAddDialog` (same `onKeyDown` on the `role="dialog"` div). TDD: a test in
   `SearchPalette.test.tsx`-style extra file (not the locked T-P0-25-xx tests) that Tab from the last control wraps to the first.
4. `SearchPalette.tsx` line ~84: Escape during IME composition closes the palette. TODO: `!event.nativeEvent.isComposing`
   (apply to `QuickAddDialog` too; also fine: return early when composing). Add a test.

Then: `/usr/bin/git push origin HEAD:wp/P0-25`, reply in each thread with the fix SHA and resolve it (GraphQL
`resolveReviewThread`; list threads with the `reviewThreads` query), comment `@coderabbitai review`, poll
`gh api repos/SpaceshipCreative/tumnis-guide/pulls/65/reviews` and `.../comments`, repeat until no unresolved threads and
CI is green (rules: ~/tumnis-coordinator/pr-review-loop.md). Never merge; the coordinator does.

## Exact next steps

1. Implement comments 3 and 4 (TDD), `make check` (semgrep env prefix below; or `npx vitest run --maxWorkers=3` plus
   `npm run lint`, `npx tsc --noEmit`, `npx prettier --check src e2e` in `frontend/`), commit, push.
2. If a UI change could affect the e2e paths (dialog key handling), rebuild the local stack and run `make e2e` again.
3. Reply/resolve threads, request re-review, watch CI, final report (PR URL, each comment with SHA or reason, CI per job,
   Scott items). When done, delete this file in a `chore:` commit and push.

`SEMGREP_SETTINGS_FILE=/tmp/claude-1002/semgrep-settings.yml SEMGREP_LOG_FILE=/tmp/claude-1002/semgrep.log SEMGREP_VERSION_CACHE_PATH=/tmp/claude-1002/semgrep-version make check`
(literal paths, no `$TMPDIR`: the worktree guard refuses commands with runtime-computed values).
Tool quirks: heredocs and `git ... -- path` forms with `cd &&` are refused in this worktree; use Write/Edit for files and
plain `/usr/bin/git` commands from the worktree root.

## Decisions and deviations (cumulative, all are in the PR body)

- Continuation 0 (unchanged): xstate 5.33.2, @xstate/react 6.1.0, idb 8.0.3 pins; `sendQueued` deletes from IndexedDB inside
  the Web Lock; `setSenderHooks`; in-memory idb fallback and `persistent()`; Mod+K works in fields, `/` does not; typeahead
  = server hits then cached projects; phone FAB moved to the shell (`QuickAddFab.tsx`); `useCreateTask` removed, composer
  enqueues; pending rows `pending:<key>`; `sessionProbeOptions` `networkMode: "always"`; `online` also invalidates errored
  queries; `performance.mark("tumnis:quickadd-ready")`; T-13 uses `serviceWorkers: "block"`; T-14 checks both directions.
- Continuation 1: `e2e/fixtures.ts` `signedInPage` opens `/` after sign-in; `recordedWrites` filters by method/path and only
  since the last reset; hotkeys install from `main.tsx` (`installHotkeys`, `appHotkeys`) as well as in the host; dialogs close
  when the host becomes bare (`/login`); quick add loads the project list itself and `lib/knownProjects.ts` keeps `{id,name}`
  in localStorage (cleared on 401); dashboard and project loaders use `lib/loader.ts` (`networkMode: "always"`, `retry: false`).
  The seed's search index is empty in the e2e stack, so the server typeahead finds nothing there.
- The PR body (`/tmp/claude-1002/p025-body.md`, applied with `gh pr edit 65 --body-file`) already describes all of the above;
  append the two machine fixes and the two dialog fixes when they land.

## Scott items

- **A0.1 (`frontend/e2e/journeys/J2.spec.ts`, first test) cannot pass as written; its `test.fail()` stays.** Step 11 asserts
  the move's `board_rank` differs from the task's; the task is first in a fresh project's Backlog (`a0`) and Today is empty,
  so `planMove` gives `a0` (T-P0-24-03 locked: empty column gives `a0`). With that one assertion skipped, steps 1 to 12 pass on
  phone and laptop. Options: relax the assertion, or change the first rank a server-created task gets. Not touched.
- "Preview checked on the phone with airplane mode on and off" (done checklist) needs a human.
- Mention the both-directions R-38 sweep in T-P0-25-14.
