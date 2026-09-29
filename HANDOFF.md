# P0-24 handoff: Project page with Tasks and Board views

Worktree `/Users/sjordan/Projects/Tumnis-Guide-wt/p0-24`, branch `wp/P0-24`. Read the
footer rules in the coordinator's prompt, AGENTS.md, CLAUDE.md and the plan section
`#### P0-24` (docs/IMPLEMENTATION-PLAN-DETAILED.md, line ~6503) first.

## Done (commits)

- `91b07b6` chore(frontend): pin `@dnd-kit/core` 6.3.1, `@dnd-kit/sortable` 10.0.0,
  `@dnd-kit/utilities` 3.2.2 (devDependencies, like every frontend dep here).
- `7c46e63` test: all 17 P0-24 spec tests red (Vitest `test.fails`, Playwright
  `test.fail()`, pytest `xfail(strict, "spec:P0-24")`), plus test support:
  `frontend/src/test/msw/project.ts` (`ProjectFake`: stateful MSW fake of project, tasks,
  board, columns, comments, brief, recurrence, undo; `refuseMoves`, `createGate`,
  `sent(method, path)`), `makeBoard` in `test/factories.ts`, e2e helpers `boardCards`,
  `openProject`, `recordWrites` in `e2e/fixtures.ts`, `components/project/types.ts`
  (`Project`, `Task`, `Board` = zod output types).
- `89194c2` feat: `grouping.ts`, `sorting.ts`, `board/move.ts` implemented. T-01, T-02,
  T-03 green (markers removed).
- `6dc3c95` feat(tasks) backend: revision `tasks_0003` (`task_changes`, down_revision
  `tasks_0001`), model `TaskChange`, rules `UNDO_FIELDS`, `undo_snapshot`,
  `change_between`, `restore_values`; api `record_change`, `_record`, `_with_change`,
  `trash_task`, `undo_task`, `list_comments`, `TaskOut.change_id`; every write (create,
  update, status/transition, move, trash, undo) records a change; payload
  `TaskStatusChangedV1.via: Literal["undo"] | None`; routes `DELETE /v1/tasks/{id}` (body
  `{version}`), `POST /v1/tasks/{id}/undo` (`auth="session"`), `GET
  /v1/tasks/{id}/comments` (paginated, `lookup:tasks`). ruff + mypy clean. NOT yet run:
  `make gen`, the undo integration test, the meta/route/table/authz sweeps.
- `0cd577e` merge of main 881a28e (PR #40: `order=today|created`, `total`, `TaskPage`;
  dashboard queries use generated options). Merged cleanly; `make gen` not yet re-run.

## Spec tests

Green: T-P0-24-01, 02, 03.
Red (marker still on): 04, 05, 17 (Playwright), 06, 07, 08, 09, 10, 11, 13, 14, 15, 16
(Vitest), 12 (pytest; now should turn green once `make gen` + sweeps pass: remove the
xfail and run it).

## Remaining TDD steps, in order

1. `make gen` (after the API changes and the merge), commit `frontend/src/api`,
   `schemas/`, generated contract tests. Check `schemas/events/v1/task.status_changed`
   picked up `via`.
2. Backend green: remove xfail from `test_undo.py` (T-12), run it; run unit, contract and
   integration layers. Expect to fix: table registry / `row_factory.COLUMN_VALUES` for
   `task_changes` (jsonb + unique change_id), the authz matrix (`tests/meta/_authz.py`)
   for the three new routes (undo is `auth="session"` without `project_param`), route
   registry (DELETE with a body is fine), live map on the frontend
   (`tasksListComments` -> `LIVE_MAP.task.details`, `lib/live-map.ts`, T-P0-22-11).
3. Knowledge routes for the Brief rail (not yet written; the plan says the rail PATCHes
   `/v1/knowledge/documents/{id}`, which no WP built): in `knowledge/router.py` add
   `GET /v1/projects/{project_id}/brief` (op `knowledge_get_brief`, `context:read`,
   `project_param="path:project_id"`, returns `DocumentDTO`) and `PATCH
   /v1/knowledge/documents/{document_id}` body `{body_md, version}` (op
   `knowledge_update_document`, `knowledge:write`, idempotent, versioned, only
   `kind == "text"` rows, `lookup:knowledge` registered with `register_project_lookup`;
   `mark_changed(s, "project", project_id)`). Put `knowledgeGetBrief` in
   `LIVE_MAP.project.details`. `make gen`. P1-14 works on knowledge in parallel: expect a
   merge conflict in `knowledge/router.py`.
4. Frontend, following the plan's TDD sequence (steps 3 to 9). Contracts the red tests
   pin (read the tests; do not edit their assertions):
   - `ProjectHeader({project, today})`: h1 name, goal, `HealthBadge`, "Next milestone:
     Mar 20" / "No milestone set", list "Today's tasks" with estimates ("No estimate"),
     "Today: 1 h 15 min", "Nothing planned for today", "No agent yet". Use
     `components/dashboard/format.ts` (`formatDay`, `formatMinutes`).
   - Composer: textbox "New task", Enter posts `{project_id, title}` through `apiWrite`,
     optimistic row in "Up next", input cleared.
   - ViewSwitcher: laptop `tablist "Views"` with tabs Tasks/Board (`aria-selected`);
     phone `radiogroup "Views"` with radios. Use `matchMedia("(min-width: 768px)")` (tests
     stub it via `setViewport`). View = `search.view ?? lastView[projectId] ?? "tasks"`;
     switching navigates `?view=` and `uiStore.trigger.setLastView`.
   - TasksView: five `region`s ("Today", "Up next", "Waiting on you", "Waiting on agent",
     "Done recently"), rows are `listitem`s; row title is a button named the title that
     opens the drawer (`?task=`); status buttons "Start <title>", "Done <title>".
   - BoardView: one `region` per column named the column; cards are `<li
     data-board-card data-task-title data-board-rank aria-label=<title…>
     aria-roledescription="draggable task" tabIndex=0>`; each card has a "Move" button
     opening a menu of `menuitem`s named after the other columns (J2's phone path).
     DndContext with MouseSensor (no activation constraint, interactive children stop
     mousedown propagation), TouchSensor (delay 250, tolerance 5), KeyboardSensor with
     `sortableKeyboardCoordinates`. Playwright `dragTo` sends ONE mousemove: on a mouse
     drag compute the drop column/index from a pointer position you track yourself
     (window `pointermove` listener while dragging) rather than `over`, which may be
     stale. Announcements: "<title> was dropped in <column>" (T-05 reads
     `[id^="DndLiveRegion"]`). `useMoveTask`: optimistic reorder in the board query,
     rollback on error, 409 `transition_not_allowed` toast from a copy map by status
     ("In review tasks can go back to In progress or to Done").
   - Rail (`aside` "Context" on laptop; phone: button "Context" opens `dialog "Context"`
     with a "Close" button): section buttons whose names start with Brief / Connections /
     Schedule / Settings and show one-line summaries ("Logo refresh for Acme", "1 person ·
     1 domain", "1 recurring task", "Subtask threshold: workspace default"),
     `aria-expanded`. Brief: textbox "Brief", "Save brief" -> PATCH
     `/v1/knowledge/documents/{id}` `{body_md, version}`. Settings: spinbutton "Subtask
     threshold (minutes)", placeholder "30 (workspace default)", "Save settings" -> PATCH
     `/v1/projects/{id}` `{subtask_threshold_min, version}`. Schedule: `GET
     /v1/recurrence?project_id=` items as buttons (title + "Weekly") opening `?task=`.
   - TaskDrawer (`dialog` named the task title, "Close" button): textbox "Title", "Save"
     (PATCH through `useUpdateTask`), "Move to trash" (DELETE `{version}`), comments
     (`GET/POST /v1/tasks/{id}/comments`), RecurrencePicker: combobox "Repeats" (never,
     daily, weekdays, weekly, monthly), combobox "Day" (0 = Monday), input "Time"
     (default 09:00), "Save repeat" -> PUT `/v1/tasks/{id}/recurrence` `{preset, weekday,
     due_time, version: <task version>}`, then "Repeats weekly on Monday at 09:00".
   - Undo: `lib/undo.ts` (`undoStore`, `undo(entry)`), `components/common/UndoToast.tsx`
     (`role="status"` named "Undo", label text + "Undo" button), Mod+Z (Ctrl or Meta + z
     outside inputs). Labels: "Marked done", "Moved to <column>", "Title changed",
     "Moved to trash". Every task mutation pushes `{changeId, afterVersion}` on success;
     after undo, refetch task lists and boards. Mount the toast in `AppShell`.
   - `/tasks` (routes/tasks.tsx): h1 "Tasks", `list "Tasks"` of `li data-task-title`
     with the project name; all pages of `GET /v1/tasks` (limit 200, follow cursors),
     sorted client-side with `sortByDue`; search `{status?, project?}`.
   - Recurrence seam (P0-19 builds the routes in parallel, not generated yet): hand
     queries with keys `[{_id: "tasksListRecurrence", query}]`; shape in
     `test/msw/project.ts` (`RecurrenceStub`). Do NOT add not-yet-generated ops to
     LIVE_MAP (T-P0-22-11 fails on unknown ops).
5. e2e: own compose project (`-p tumnis-p024`, unique `TUMNIS_TEST_PORT`, image
   `tumnis:p024`), move `frontend/dist` aside around backend layers, tear down after.
   Remove `test.fail()` from board.spec.ts and layout/project.spec.ts when green. J2
   (A0.1) board steps must pass (its marker stays until P0-25).
6. Update Part A (A12) with the P0-24 names; `make check`, `npm --prefix frontend run
   typecheck`, all layers.

## Decisions and deviations (report these)

- dnd-kit: `@dnd-kit/react` 0.5.0 injects a `<style>` element during drags, which the
  strict CSP (`style-src 'self'`) blocks; switched to the plan's named fallback
  `@dnd-kit/core` + `@dnd-kit/sortable`.
- `order=due` on `GET /v1/tasks` not added (PR #40 just changed that route's `order`
  param): `/tasks` sorts client-side with `sortByDue`. Could now add `due` to the merged
  `order` enum if wanted.
- New backend routes not listed in the plan: `DELETE /v1/tasks/{id}` (trash; the plan
  names trash but no route), `GET /v1/tasks/{id}/comments` (CommentList had no read),
  and (step 3, to do) the brief read + text-document PATCH.
- Undo of a create trashes the task (change `{deleted: true} -> {deleted: false}`).
- T-P0-24-06 and T-11 exercise the move through the card's Move menu (same planMove and
  move request as a drag); jsdom cannot drag.
- tasks_0003 chains after tasks_0001; P0-19's tasks_0002 needs reordering at merge.
- Recurrence PUT sends the task's version for a new rule (P0-19 undefined).

## Gotchas

- Output is filtered by the rtk hook: use `rtk proxy uv run pytest ...` to see pytest
  output.
- `vi.resetModules()` in T-08/T-09 re-imports the route tree; module-level state
  (uiStore) must read storage through `lib/storage.ts` (never throws).
- ProjectFake's generated reads are zod-validated by the SDK: test data must match the
  schemas; `change_id` appears in `zTaskOut` only after `make gen`.

## Verify

```bash
cd frontend && npx vitest run src/components/project src/components/board src/lib/undo.test.tsx
npm run typecheck && npx eslint src e2e
cd ../backend && rtk proxy uv run pytest tumnis/modules/tasks/tests/integration/test_undo.py -m integration
uv run pytest -m "not integration and not contract" -n auto; uv run pytest -m contract; uv run pytest -m integration -n auto
cd .. && make check
```
