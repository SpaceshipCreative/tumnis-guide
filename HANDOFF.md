# HANDOFF: FIX-app-findings-2 (APP-13 archive UI, APP-14, APP-15, APP-16)

- PR: https://github.com/SpaceshipCreative/tumnis-guide/pull/165, branch `fix/app-findings-2`. It is ready for review, not a draft.
- Worktree setup: the agent stayed on its throwaway branch and pushed with `/usr/bin/git push origin HEAD:refs/heads/fix/app-findings-2`. A fresh agent should run `git fetch origin && git switch -c agent/app2 origin/fix/app-findings-2`, or merge it into its throwaway branch.

## Commits
| SHA | What |
| --- | --- |
| 918f86d1 | APP-14 and APP-15: the phone TaskDrawer and the ContextSheet trap focus (`dialogKeyDown`); the uiStore `modalSheets` counter; QuickAddFab hidden while a sheet is open |
| e73eecfd | APP-13 (archive UI): `ArchiveProject.tsx` (`useArchiveProject`, `ArchiveControl`, `ArchiveBadge`, `ArchivedProjects`), wired into rail SettingsSection, ProjectHeader and ProjectList |
| 2b561c9d | APP-16 red test (CI run 36972502285, integration-b: failed as expected) |
| 9c368cee | APP-16 fix: `cancel_removed_workflows` in `backend/tumnis/core/testing_routes.py` |
| e38d8c30 | CodeRabbit fixes: the archived list follows `next_cursor` (infinite query); the APP-16 test asserts `CANCELLED` |
| c0e19762 | merge of origin/main (#163, #164) |

## Tests
- Vitest:
  - `frontend/src/components/project/drawer/TaskDrawer.modal.test.tsx` (APP-14, APP-15)
  - `frontend/src/components/project/ArchiveProject.test.tsx` (APP-13: Settings archive and unarchive at both viewports; the archived list unarchives at both viewports; the list reads every page)
- pytest: `backend/tests/harness/test_acceptance_runtime.py::test_a_reset_ends_every_workflow_of_the_world_it_removed` (APP-16; integration-b)
- Local: `make check` was green at e38d8c30 (with SEMGREP_* env files under $TMPDIR). After the merge, full Vitest (366) and tsc are green.
- CI: run 36973382023 (at 9c368cee) was green on every job. Run 36975214417 (at c0e19762) was queued at handoff time.

## CodeRabbit
- First review: 3 comments, all replied to and resolved.
  - test_acceptance_runtime:514: assert CANCELLED. Fixed in e38d8c30.
  - ArchiveProject.tsx:213: pagination. Fixed in e38d8c30.
  - testing_routes.py:265: "quiesce running steps before seeding". Declined with a reason. The route is test-only, DBOS has no wait-for-step API, and a late step can't write into the new world because the seed gives it a fresh uuid7 workspace id and the step's write fails its foreign key. Strict quiesce is a possible Scott follow-up.
- `@coderabbitai review` was requested again after the fixes. CodeRabbit's review said its quota ("all 10 included reviews") was used up, so it may not re-review.

## Remaining steps
1. Wait for CI run 36975214417. Use `bash /tmp/claude-1002/FIX-app-findings-2/wait_run.sh 36975214417`, or `gh pr checks 165`. Don't wait on `preview`.
2. Check for a new CodeRabbit review or comments: `gh api repos/SpaceshipCreative/tumnis-guide/pulls/165/reviews` and `/comments`, plus the GraphQL reviewThreads query. Handle any new threads.
3. Merge origin/main again before the last push. #162 (FIX-app-findings) changes `truncate_tables` in the same `testing_routes.py`; the hunks don't overlap.
4. When CI is green and no threads are open, send main: `#165 MERGE-READY at <sha>`.

## Decisions and deviations
- APP-13 coordinator defaults (also in the PR body):
  - Archive and Unarchive sit in the project rail's Settings section.
  - Archive asks for confirmation inline, not in a modal. The section sits inside the phone Context sheet, which is itself a modal, so a nested modal's Escape would also close the sheet.
  - The archived list is a collapsed "Show archived projects" section on /projects.
  - The header badge reads Archived, Archiving or Restoring.
- APP-14: the ContextSheet got the same focus trap. This goes beyond the literal finding.
- APP-16:
  - The function is renamed `cancel_removed_deliveries` → `cancel_removed_workflows`. No test referenced the old name; main approved the edit.
  - It cancels every ENQUEUED, PENDING or DELAYED workflow except a schedule's runs (`schedule_name` set), as the coordinator asked.
- Initial JS is 193.8 KB gzipped against the 200 KB budget.
- Vitest `RunView.test.tsx` failed once in a full local run under load and passed alone and in later runs. It looks flaky; it isn't touched by this PR.

## Scott items
- None blocking.
- Optional: decide whether test resets should strictly quiesce in-flight workflow steps (the CodeRabbit comment above).
