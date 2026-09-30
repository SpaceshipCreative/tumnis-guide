# P1-12 handoff (Project Calendar view)

Branch: `wp/P1-12` (pushed with `/usr/bin/git push origin HEAD:wp/P1-12`). No PR yet, so
no review threads and no CI on a PR yet.

## Commits
- `103aee1` test(planning): P1-12 spec tests (red). T-P1-12-01 to 08 as strict expected
  failures, with interface stubs, generated client and live-map entries.
- `980d37a` feat(planning): event_matches_project (T-P1-12-07). Marker removed, 11 cases green.
- `6e85b8e` feat(planning): validate_manual_block, plus a non-spec unit table
  (`tests/unit/test_validate_manual_block.py`, 12 rows green).
- `86b52ff` feat(planning): week endpoint and manual blocks. Migration `planning_0002`
  (daily_plans, plan_items), `project_week`, `schedule_block`, and seams:
  `calendar.api.EventOut.attendees`, `projects.api.project_links`, `tasks.api.project_tasks`.
  The row_factory COLUMN_VALUES gain the daily_plans check values.
- (this commit) chore: P1-12 handoff. It also holds the frontend WIP: `lib/time.ts` (+ a
  green unit test), the CalendarView implementation, the `calendar` view in
  ViewSwitcher/ProjectPage/views.ts, the route's `week` search param, and
  `planningGetProjectWeek` in task-cache. `tsc --noEmit` passes. Vitest and ESLint have
  NOT been run on the finished CalendarView.

## Spec test state
- T-P1-12-07: green, marker removed.
- T-P1-12-06, T-P1-12-08 (integration): implementation written, markers still ON. A
  `make test-int` run was started locally before the handoff, and its result is unknown.
  Next: run `make test-int` bare from the worktree root (or rely on CI). XPASS(strict) on
  these two means they pass, so remove their `@pytest.mark.xfail(strict=True, reason="spec:P1-12")`
  lines only after a run proves it.
- T-P1-12-01..05 (Vitest, `frontend/src/components/project/CalendarView.test.tsx`):
  markers still `test.fails`. Next: `cd frontend && npx vitest run src/components/project/CalendarView.test.tsx`
  with the markers flipped one at a time (`test.fails` -> `test`), and fix the view until each passes.

## Remaining steps
1. `cd frontend && npx eslint src/components/project src/lib src/routes`, then fix the
   findings (the `!` non-null assertion was removed; check `aria-describedby={undefined}`,
   unused imports and the `contents` class on the grid lists).
2. Flip T-P1-12-01..05 one at a time. Known risk: T-02/T-03 drive dnd-kit's MouseSensor
   with `user.pointer` over rects that `src/test/calendarLayout.ts` stubs (1 px per minute,
   100 px per day column, from `data-col`, `data-minute`, `data-minutes` and `data-card`).
   CalendarView puts those attributes on slots, events and planned blocks. Collision is
   `pointerWithin`; a busy drop finds `[data-busy]` rects under the end point.
3. Integration: run `make test-int`, remove the markers for 06 and 08 once they pass, and
   check that the route registry, authz matrix and table registry meta tests stay green
   (new routes: `GET /v1/plan/week/{monday}`, `PATCH /v1/plan/{day}/items/{task_id}`;
   new tables: daily_plans, plan_items).
4. `make gen` (EventOut gained `attendees`, but it isn't in any response, so no diff is
   expected), `make check` with SEMGREP_* env vars under /tmp/claude-1002/semgrep/.
   Vitest timeouts under heavy VM load (ProjectList, CalendarStrip, WorkingHours) pass when
   run alone: they come from load, not from this change.
5. Push, open the PR (`[P1-12] impl: Project Calendar view`), comment `@coderabbitai review`,
   and run the review loop.

## Decisions and deviations (for the PR body)
- The coordinator's prompt said "one PATCH through the existing tasks API". The plan and
  the locked spec T-P1-12-02 specify `PATCH /v1/plan/{day}/items/{task_id}`, and tasks has
  no schedule fields, so the plan's endpoint was followed.
- P1-11 (the plan tables) has not landed. As the smallest seam, `planning_0002` creates
  `daily_plans` and `plan_items` exactly per P1-11's SQL (plan_issues and plan_pins are
  left to P1-11), with a partial unique "one published per day" index. plan_items.task_id
  has no FK (purge_trash hard-deletes tasks). **Alembic revision: `planning_0002`
  (down `planning_0001`).**
- PlanTask/Violation defined in planning rules per P1-11's interface.
- The week payload adds `monday`, `timezone` and `unscheduled` (open Human/Hybrid tasks with
  an estimate and no block this week) to the plan's shape. The `planned` entries are
  `{task_id, title, start, end}`, with task_id and title null for another project's block.
- A 409 carries `current = {free_blocks, violations}` (ProblemError only supports `current`).
- Keyboard scheduling is the view's own slot cursor, not dnd-kit's KeyboardSensor: the slots
  are discrete, and coordinate-based collision has no layout in jsdom. Pointer and touch use
  dnd-kit MouseSensor/TouchSensor with `pointerWithin`.
- The view's live region is named "Scheduling", because dnd-kit's DndContext renders its
  own unnamed role=status.
- T-P1-12-06/08 sign in as the seed user (seed workspace) rather than use the `workspace`
  fixture, because the seed's calendar day holds the 13:00-14:00 busy event.
- The MSW week payload is hand-written synthetic data (`src/test/msw/week.ts`), not generated
  from the seed.
- Shared-file edits: none of the listed shared files (pyproject, uv.lock, Makefile,
  AGENTS.md, .importlinter, package.json).

## Scott items
- None blocking. Scott decision 5 (all-day busy rule) is not hard-coded here: the week uses
  each event's `busy` flag as stored.

## Context7 docs checked
- dnd-kit legacy (@dnd-kit/core 6.x) docs, /clauderic/dnd-kit: useSensors, MouseSensor
  and TouchSensor activation constraints, pointerWithin collision detection.
- TanStack Query v5 optimistic updates (onMutate/cancelQueries/setQueryData, rollback in
  onError, invalidate in onSettled).
- Still to check: TanStack Router search validation with zod `.catch` (the pattern was
  copied from the existing route).

## Verify commands
- `cd backend && uv run pytest -q -m "not integration and not contract" tumnis/modules/planning`
- `make test-int` (bare, from the worktree root)
- `cd frontend && npx vitest run --maxWorkers=3 src/components/project/CalendarView.test.tsx src/lib/time.test.ts`
- `make check` (with SEMGREP_SETTINGS_FILE, SEMGREP_LOG_FILE, SEMGREP_VERSION_CACHE_PATH under /tmp/claude-1002/semgrep/)
