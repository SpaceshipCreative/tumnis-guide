# P0-24 handoff 5: PR #57 review loop (VM reboot)

PR: https://github.com/SpaceshipCreative/tumnis-guide/pull/57 (`wp/P0-24` -> main). Do not merge.
Local branch in the agent worktree: `agent/P0-24`, pushed with
`git push origin agent/P0-24:wp/P0-24` (never force). To resume in a fresh worktree:
`git fetch origin` then `git switch -c agent/P0-24 origin/wp/P0-24`. On this VM, `git` in
Bash goes through an rtk wrapper that the worktree guard rejects. `/usr/bin/git ...` works.
`.git/config` is read-only, so the upstream can't be set; that's harmless.

## Commits this round (after ae1688f, handoff 4)

- `8a34289` Merge origin/main (#50, P0-19).
  - `tasks/api.py`: imports unioned. `_transition` records the undo change, then makes the recurrence successor. `_insert` records the create-undo; P0-19's `_announce_created` is unchanged, so tick-made successors aren't undoable.
  - `live-map.ts`: task details are `tasksGetTask`, `tasksListComments` and `tasksGetRecurrence`.
  - Generated client and `schemas/openapi.json` regenerated with `make gen`.
  - `tasks_0003.down_revision = "tasks_0002"`: a single tasks head.
- `60bbd18` test(projects): unmarked T-P0-24-04, 05 and 17 (Scott-approved step).
- `92b8594` chore: removed the old HANDOFF.md.
- `5919d97` test(tasks): `tests/unit/test_undo_rules.py`. CI's unit coverage gate needs 100% on `tasks/rules.py`, and the undo rules were only reached by integration tests.
- `b806b17` fix(tasks): `task_changes.task_id` FK is now `ON DELETE CASCADE` (edited in `0003_task_changes.py`, which is unreleased). P0-19's trash purge (`test_trash_purge_after_retention`) failed on the FK. **Not yet verified in CI.**
- `7888802`, `5560a56`, `2dc942b`: the CodeRabbit fixes (below).
- The commit that adds this file.

## CodeRabbit (review 5358441815, on 92b8594): 6 inline comments, all fixed

Fixed in code but **not yet replied to or resolved**. Next agent: reply with the SHA in each thread, then resolve it with the GraphQL `resolveReviewThread` mutation.

| Comment id | File | Fix |
| --- | --- | --- |
| 4138308722 | `board/BoardCard.tsx` | Escape returns focus to Move (`2dc942b`, test `BoardCard.test.tsx`) |
| 4138308734 | `common/NoticeToast.tsx` | `role="status"` (`2dc942b`) |
| 4138308743 | `common/UndoToast.tsx` | same fix as the next row (`7888802`) |
| 4138308784 | `lib/undo.ts` | drop on success, 409 or 404; keep on other errors (`7888802`, test `undo-retry.test.ts`) |
| 4138308751 | `project/queries.ts` | `projectTasksQuery` follows `next_cursor`; returns a TaskPage with every item (`5560a56`, test `ProjectTasksPages.test.tsx`) |
| 4138308760 | `project/ViewSwitcher.tsx` | arrows move focus; tab ids plus `aria-controls`; laptop `tabpanel` in `ProjectPage` (`2dc942b`, test `ViewSwitcher.test.tsx`) |

The review body had no outside-diff or nitpick sections.

## CI state (on 92b8594, before the fixes above)

- Green: lint, contract, e2e, spec-guard, traceability, security, red-proof, version-skew, skills, performance, GitGuardian.
- Pending: `preview` (expected: no homelab runner yet).
- Red:
  - unit: the coverage gate, fixed by `5919d97`.
  - integration: `test_trash_purge_after_retention`, fixed by `b806b17`. 707 other tests passed.
- The coordinator says `integration` can be cancelled at its 10:00 budget even when every test passes. That's main-wide, and `fix/ci-integration-budget` owns it. If so, run `gh run rerun <id> --failed` once and note it.

## Local results

- `make check`: ruff, mypy, import-linter, ESLint, tsc and 951 unit tests are green. Vitest is 69 tests. Under load (load average 34 to 42), a few long Vitest tests time out (T-P0-24-11, T-P0-24-16, a P0-17 list test); each passes when rerun on its own.
- Contract: 106 passed in the sandbox. The two Docker-backed contract tests need a bare run.
- Playwright: full suite (on `60bbd18`) 41 passed, 3 skipped by design, 0 failed.
  - Not rerun after `2dc942b`, which wraps the laptop view in a `tabpanel` div (`min-w-0`).
  - CI e2e will show whether that changes T-P0-24-17's layout.
- Integration: not run locally.
  - Docker-backed pytest only runs bare from `backend/`, and an agent thread's cwd resets between calls.
  - `make test-int` works bare but uses `-n auto`, which breaks the `-n 3` load rule.
  - CI is the check.
- Playwright bare path that works: `make up`, then `make e2e` (the whole suite), then `make down`.
  - `npx playwright` from the root picks up a different Playwright.
  - `npx --prefix frontend playwright ...` is sandboxed and can't reach localhost:8080.
  - The unsandboxed retry was denied by the permission classifier.

## Next steps

1. Push is done with this file. Comment `@coderabbitai review` on #57.
2. Reply to and resolve the 6 threads above (SHAs in the table).
3. Wait for CI. Check that unit and integration go green, and that e2e passes with the tabpanel wrapper.
4. Loop on any new CodeRabbit comments until none are unresolved and CI is green (apart from `preview`).
5. Delete this file in a final `chore:` commit and push.

## Deviations (all in the PR body, plus these from this round)

- `task_changes` FK cascades on delete (the trash purge), matching P0-19's comments and context links.
- `projectTasksQuery` now reads every page. Before, it read only the first page of 200.
- Laptop view content sits in a `role="tabpanel"` div. The phone rendering is unchanged.
- The notice toast is `role="status"`, not `alert`.
- The frontend recurrence reads are still hand queries (follow-up; noted in the PR body).

## For Scott

- The integration layer and 2 Docker contract tests can't run locally from an agent thread (see Local results); CI covers them.
- The `preview` check stays pending with no homelab runner.
