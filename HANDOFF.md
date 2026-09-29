# P0-24 handoff 2: Project page with Tasks and Board views

Worktree `/Users/sjordan/Projects/Tumnis-Guide-wt/p0-24`, branch `wp/P0-24`. Read the
coordinator's footer rules, AGENTS.md, CLAUDE.md and the plan section `#### P0-24`
(docs/IMPLEMENTATION-PLAN-DETAILED.md, line ~6503; use offset/limit) first. Do not redo
committed work; trust `git log --oneline main..HEAD`.

## Done (commits)

First agent:
- `91b07b6` pin `@dnd-kit/core` 6.3.1, `@dnd-kit/sortable` 10.0.0, `@dnd-kit/utilities`
  3.2.2 (devDependencies).
- `7c46e63` all 17 spec tests red; test support `test/msw/project.ts` (ProjectFake),
  `makeBoard` in `test/factories.ts`, e2e helpers `boardCards`, `openProject`,
  `recordWrites` in `e2e/fixtures.ts`, `components/project/types.ts`.
- `89194c2` grouping.ts, sorting.ts, board/move.ts (T-01, 02, 03 green).
- `6dc3c95` backend: `tasks_0003` (`task_changes`), `record_change`, `undo_task`,
  `trash_task`, `list_comments`, `TaskOut.change_id`, routes `DELETE /v1/tasks/{id}`,
  `POST /v1/tasks/{id}/undo`, `GET /v1/tasks/{id}/comments`.
- `0cd577e` merge of main 881a28e.

Second agent (this one):
- `7bcaa72` `make gen`; also restored the `Page` import the merge dropped from
  `tasks/router.py`.
- `739f05b` T-P0-24-12 green (xfail removed; `test_undo.py` passes).
- `2190474` `task.status_changed` leaves `via` out unless an undo set it
  (`Field(exclude_if=...)`); the locked P0-18 test `test_task_events.py` pins the payload
  without `via`.
- `5811d3c` knowledge routes for the Brief rail, appended as a separate block:
  `GET /v1/projects/{project_id}/brief` (op `knowledge_get_brief`, `context:read`,
  `path:project_id`) and `PATCH /v1/knowledge/documents/{document_id}` `{body_md, version}`
  (op `knowledge_update_document`, `knowledge:write`, idempotent, `lookup:knowledge`; only
  `kind == "text"`, else 409 `not_text`; stale is 409 `stale_version` with the current
  DocumentDTO). `knowledge/api.py` got `project_of`, `register_project_lookup("knowledge")`,
  `update_text_document` in a block at the end of the file; `knowledge/router.py` had only
  a docstring on main and now holds `router = v1_router("knowledge", tags=["knowledge"])`
  (no prefix) plus the two routes. New integration test
  `knowledge/tests/integration/test_text_documents.py` (green). LIVE_MAP: `knowledgeGetBrief`
  in `project.details`, `tasksListComments` in `task.details`.
- `f09ec83` ProjectHeader (T-13 green).
- `5707f3f` `TaskOut` gets `ConfigDict(json_schema_serialization_defaults_required=True)` so
  `change_id` and `schema_version` are required in the generated types; without it the
  zod `.nullish()` output and the TS `change_id?: string | null` disagreed under
  `exactOptionalPropertyTypes` and broke the typecheck of the existing
  `lib/optimistic.test.tsx`. `make gen` output committed.
- `706cb34` `vitest.config.ts` `testTimeout: 20_000` (T-11 renders the page eight times
  and passes ~4.8 s alone, over 5 s in the full suite).
- `2f7bd46` the frontend: ProjectPage, Composer, ViewSwitcher, TasksView/TaskGroup/TaskRow,
  BoardView/BoardColumn/BoardCard/Checklist, rail (RailSection, RailSections, RightRail,
  ContextSheet, Brief/Connections/Schedule/Settings sections), drawer (TaskDrawer,
  RecurrencePicker, CommentList), `components/project/{queries,mutations}.ts`,
  `lib/{undo,task-cache,media}.ts`, `components/common/{UndoToast,NoticeToast}.tsx`
  (mounted in AppShell), routes `projects.$projectId.tsx` (loader + page) and `tasks.tsx`
  (all pages, sorted by `sortByDue`), `uiStore` (`notice`, `showNotice`, `clearNotice`,
  `setContextSheet`; lastView persistence now merges the changed entries into what storage
  holds instead of writing the whole in-memory map, which T-08 needs), `useUpdateTask`
  pushes an undo entry and refreshes every task view, ProjectFake aligned with P0-19
  (below) and answers `total`. Markers removed: T-06, 07, 08, 09, 10, 11, 14, 15, 16.

## Spec tests

Green: T-P0-24-01 to 03, 06 to 16 (Vitest 63/63; pytest T-12).
Still marked (`test.fail()`): T-P0-24-04, 05 (`e2e/board.spec.ts`) and T-P0-24-17
(`e2e/layout/project.spec.ts`). They have NOT been seen green yet: see "E2E blocker".

## Verified green at 2f7bd46

- Vitest: 36 files, 63 tests.
- `npm --prefix frontend run typecheck`: clean. `npm --prefix frontend run lint` (ESLint +
  prettier --check): clean.
- Backend unit 853 passed; contract 83 passed.
- Backend integration: 619 passed / 1 failed before `2190474`; that failure
  (`test_task_events.py`) is fixed and re-run green. The FULL integration layer was not
  re-run after `5707f3f` and `5811d3c`: re-run it.
- `make check`: not yet run as a whole (its parts above are green).

## E2E blocker (next step)

Stack: `TUMNIS_IMAGE=tumnis:p024 TUMNIS_TEST_PORT=18424 docker compose -p tumnis-p024 -f
deploy/compose.test.yaml up -d --wait --build`, then
`E2E_BASE_URL=http://localhost:18424 npx playwright test e2e/board.spec.ts
e2e/layout/project.spec.ts e2e/journeys/J2.spec.ts` from `frontend/`. Tear down with the
same env and `down -v`. The stack is down now.

With the markers still on, the first run gave "5 passed (as expected failures), 1 skipped"
for the P0-24 specs, and J2 failed in its fixture: `POST /v1/test/reset` timed out (10 s).
After removing the markers every test failed the same way. `pg_stat_activity` showed a
session `idle in transaction` on `SELECT * FROM app.outbox_claim($1)` (the outbox relay)
holding locks for minutes, the reset's `TRUNCATE` queued behind it, and every other query
(board reads, dead_letters inserts, later resets' `ALTER TABLE audit_log DISABLE TRIGGER`)
queued behind the TRUNCATE. The api log also showed one
`DeadlockDetected` between the TRUNCATE and a `board_columns` read. So a reset while the
relay holds its claim transaction wedges the stack. Next steps:
1. Check whether this happens on main too (build main's image, run `e2e/layout/dashboard.spec.ts`
   a few times): if so, it is a pre-existing stack bug, not P0-24's; report it and restart
   the api and worker between runs (`docker compose ... restart api worker`) to get a
   clean run.
2. If it is P0-24's: suspect the extra events (every task write now records a change;
   `task.status_changed` via undo; the brief PATCH) or the page's many parallel reads
   right as the reset lands. Look at `tumnis/core/outbox.py` relay claim transaction and
   whether a subscriber blocks inside it.
3. Once the stack resets cleanly, remove `test.fail()` from T-04, T-05 (board.spec.ts)
   and T-17 (layout/project.spec.ts), run them at both widths, and fix what fails. J2's
   board steps (10, 11) must pass; its `test.fail()` stays until P0-25.
   Board facts for T-04: Acme's Today column has one seed card, so a drop at the bottom of
   Today lands at index 1. Pointer drops use the pointer position tracked on window
   `pointermove`/`mousemove`/`touchmove` (`dropAtPoint` in BoardView), keyboard drops use
   `over` (`dropAtOver`). The DndContext announces `"<title> was dropped in <column>"`.

## Remaining steps after the e2e specs

1. `git merge main`, then the Alembic check (coordinator): if main contains `tasks_0002`
   (P0-19), set `down_revision = "tasks_0002"` in
   `backend/tumnis/modules/tasks/migrations/0003_task_changes.py`; otherwise leave it on
   `tasks_0001` and say so. `cd backend && uv run alembic heads` must show one head per
   branch. After the merge run `make gen` and commit.
2. Recurrence seam (coordinator): P0-19 builds `GET/PUT/DELETE /v1/tasks/{id}/recurrence`
   and `GET /v1/recurrence?project_id=`. The frontend reads them with hand queries in
   `components/project/queries.ts` (`projectRecurrenceQuery`, `taskRecurrenceQuery`, keys
   `[{_id: "tasksListRecurrence"|"tasksGetRecurrence", ...}]`, 404 read as "none") and
   writes through `apiWrite` in `drawer/RecurrencePicker.tsx`. The shape matches P0-19's
   T-P0-19-19 (read from the p0-19 worktree's test): rule `{id, task_id, project_id,
   preset, cron, weekday, month_day, due_time "HH:MM:SS", title, latest_task_id,
   latest_occurrence_on, next_due_at, version}`, list `{items}`; PUT takes the task's
   version for a new rule (the rule's version equals the task's after), DELETE takes
   `?version=`. If P0-19 is on main after the merge, switch those reads to the generated
   options and add the ops to LIVE_MAP (T-P0-22-11 fails on generated ops missing there).
3. Update Part A (A12) of the plan with the P0-24 names (components, `lib/undo.ts`
   `remember`/`undo`, `lib/task-cache.ts`, knowledge routes, `uiStore` additions).
4. Finish: `make gen`, `make check`, `npm --prefix frontend run typecheck`, backend unit,
   contract and integration layers (move `frontend/dist` aside first if it exists), Vitest,
   the P0-24 Playwright specs on the own stack, tear down.

## Decisions and deviations (report these)

- dnd-kit: `@dnd-kit/react` 0.5.0 injects a `<style>` element during drags, which the
  strict CSP blocks; switched to the plan's fallback `@dnd-kit/core` + `@dnd-kit/sortable`.
  Cards override useSortable's role to `listitem` with roledescription "draggable task".
- No backend `order=due` on `GET /v1/tasks`: `/tasks` follows every cursor and sorts with
  `sortByDue` on the client. Its query key is the generated op's key plus `pages: "all"`
  (array data, so it never collides with a `TaskPage` cache entry).
- Extra backend routes not in the plan: `DELETE /v1/tasks/{id}` (trash),
  `GET /v1/tasks/{id}/comments`, `GET /v1/projects/{id}/brief`,
  `PATCH /v1/knowledge/documents/{id}` (the plan's BriefSection names it; no WP built it).
- Undo of a create trashes the task (change `{deleted: true} -> {deleted: false}`).
- T-06 and T-11 move cards through the card's Move menu (same `planMove`, same one move
  request as a drag); jsdom cannot drag.
- `TaskOut` schema marks defaults required (`5707f3f`); `via` excluded when None
  (`2190474`).
- Vitest `testTimeout` 20 s (`706cb34`).
- Refused-move copy is keyed by the from-status (P0-18's human edges), e.g. "In review
  tasks can go back to In progress or to Done"; same-status gets its own line.
- Board cards also carry an "Open" button (opens the drawer); the Tasks view has `j`/`k`,
  Enter, `s`, `d`.
- Connections edits people and domains (one per line) and the code path through
  `PATCH /v1/projects/{id}`; repo and Coolify links are kept as they are.
- Recurrence PUT sends the task's version for a new rule (P0-19's test confirms).

## Shared-file edits (report these)

- `frontend/package.json`: the three `@dnd-kit` pins (first agent).
- `frontend/e2e/fixtures.ts`: `boardCards`, `openProject`, `recordWrites` (first agent).
- `frontend/src/test/factories.ts`: `makeBoard` (first agent).
- `frontend/vitest.config.ts`: `testTimeout` (this agent).
- `frontend/src/stores/uiStore.ts`, `lib/optimistic.ts`, `components/common/AppShell.tsx`,
  `ConflictToast.tsx` (`brief` entity), `lib/live-map.ts`.

## Gotchas

- rtk filters output: use `rtk proxy uv run pytest ...` to see pytest output.
- Removing a `spec:` marker with `sed -i '<n>d'` was refused by the auto-mode classifier
  as test removal; read the lines first, then use the Edit tool on the exact marker.
- Prettier reformats a test once its `.fails` goes (line length); the diff is whitespace,
  trailing commas and the marker only (checked token by token). `npm run lint` runs
  `prettier --check .`, so format them.
- A label wrapping a `<select>` makes the select's accessible name include the chosen
  option ("Day Monday"); use `htmlFor`/`id` (RecurrencePicker, Settings).
- The unhandled-request strategy is "error": the page must only call endpoints the
  ProjectFake serves.

## Verify

```bash
cd frontend && npx vitest run && npm run typecheck && npm run lint
cd ../backend && rtk proxy uv run pytest tumnis/modules/tasks/tests/integration/test_undo.py tumnis/modules/knowledge/tests/integration/test_text_documents.py -m integration
uv run pytest -m "not integration and not contract" -n auto; uv run pytest -m contract; uv run pytest -m integration -n auto
cd .. && make check
```
