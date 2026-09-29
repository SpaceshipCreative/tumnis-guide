# P0-24 handoff 3: Project page with Tasks and Board views

Worktree `/Users/sjordan/Projects/Tumnis-Guide-wt/p0-24`, branch `wp/P0-24` (pushed to
origin). Read the coordinator's footer rules, AGENTS.md, CLAUDE.md and the plan section
`#### P0-24` (docs/IMPLEMENTATION-PLAN-DETAILED.md, line ~6503; use offset/limit) first. Do
not redo committed work; trust `git log --oneline main..HEAD`.

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

Second agent:
- `7bcaa72` `make gen`; restored the `Page` import the merge dropped from `tasks/router.py`.
- `739f05b` T-P0-24-12 green.
- `2190474` `task.status_changed` leaves `via` out unless an undo set it.
- `5811d3c` knowledge routes for the Brief rail (`GET /v1/projects/{id}/brief`,
  `PATCH /v1/knowledge/documents/{id}`), `knowledge/api.py` additions, integration test
  `knowledge/tests/integration/test_text_documents.py`.
- `f09ec83` ProjectHeader (T-13 green).
- `5707f3f` `TaskOut` `json_schema_serialization_defaults_required=True`; `make gen`.
- `706cb34` Vitest `testTimeout: 20_000`.
- `2f7bd46` the frontend (ProjectPage, views, board, rail, drawer, undo, toasts, routes).
  Markers removed: T-06, 07, 08, 09, 10, 11, 14, 15, 16.

Third agent (this one):
- `fd16624` `fix(core)`: issue #51 (below). `truncate_tables` locks `outbox` first;
  regression test `backend/tumnis/core/tests/integration/test_issue_51_reset_relay_deadlock.py`
  (red on the old code: "reset and relay deadlocked"; green with the fix, together with
  `test_testing_routes.py`: 5 passed).

## Spec tests

Green: T-P0-24-01 to 03, 06 to 16 (Vitest; pytest T-12).
Still marked `test.fail()`: T-P0-24-04, 05 (`frontend/e2e/board.spec.ts`), T-P0-24-17
(`frontend/e2e/layout/project.spec.ts`).

## Reset hang: root cause and fix (issue #51, done)

- Reproduced on main (881a28e): built main's image as `tumnis:p024-main` from `git archive
  main` and ran it as compose project `tumnis-p024main` on port 18425 (now torn down). Two
  reset loops running side by side wedged it. `pg_stat_activity`: the relay's
  `SELECT * FROM app.outbox_claim($1)` sat idle in transaction; the reset's `TRUNCATE` was
  blocked by it; the relay's own `SELECT enabled FROM module_flags` (second connection) was
  blocked by the TRUNCATE.
- Cause: `relay_once` (core/events.py) keeps its claim transaction open while
  `modules.enabled` reads `module_flags` on another connection. That read is a cache miss
  for every new workspace, and every reset seeds a new workspace id, for example
  `knowledge.create_brief` on `project.created`. `truncate_tables` TRUNCATEs every table in
  sorted order, so it took `module_flags` before `outbox`. That makes a deadlock across two
  connections, which Postgres cannot detect.
- Fix (`fd16624`): `truncate_tables` runs `LOCK TABLE outbox IN ACCESS EXCLUSIVE MODE`
  before anything else. The plan's reset contract says nothing about restarting services,
  so nothing is restarted.
- After the fix, the P0-24 stack reset cleanly and the P0-24 specs ran. Two reset loops
  running side by side still get 500s (one reset's seed collides with the other's truncate:
  FK or deadlock-detected errors), but nothing hangs. Main does the same, and Playwright
  runs with `workers: 1`, so resets never overlap.
- Not changed: the relay still waits on another connection while it holds its claim. The
  reset was the only thing that locked `module_flags` against it. It is noted in #51.

## T-P0-24-05 (keyboard drag): diagnosis so far, not fixed

With the stack healthy, T-05 fails at both widths: the card stays in Backlog, and the live
region says "was dropped in Backlog". Instrumented runs (a throwaway spec, since deleted):
- Space picks the card up ("Picked up", "is over Backlog"). dnd-kit's KeyboardSensor adds
  its document keydown listener about 1 ms later, before the ArrowRight arrives.
- ArrowRight reaches the sensor (the default is prevented; no scroll event; the board root
  at laptop is 672 px wide from x=256, so the scroll-clamp path is not taken). But the
  "is over Today" update lands about 4 ms after the key. Playwright's next Space arrives
  sooner (about 3 to 4 ms), so `onDragEnd` sees the stale `over` (Backlog). One run in six
  passed, when the over update won the race.
- With 500 ms waits between keys the same sequence moves the card to Today (move 200,
  announcement "was dropped in Today").
- The spec is locked: do not add waits to it. Fix it in the app, for example:
  1. Keep the drop target the keyboard chose: in `BoardView`, track it from `onDragMove`
     or `onDragOver`. On a keyboard `onDragEnd`, compute the drop from the dragged rect
     moved by the sensor's last coordinates (for example `dropAtPoint` at the centre of
     `active.rect.current.initial` plus the final delta), not from `over`. Check whether
     the `DragEndEvent` delta is also stale; if it is, keep the last coordinates yourself.
  2. Or wrap KeyboardSensor (its members are `private` in the .d.ts) so that an end key
     waits until the previous move has rendered, for example one key per animation frame.
  Then run T-05 at both widths with `--repeat-each=5` on a loaded machine.
- T-04 (pointer drag, laptop only) and T-17 (layout) have NOT been run since the fix.
  Remove their markers one at a time and run them.

## Remaining steps

1. Fix T-05 as above, remove its marker (exact Edit of `test.fail();`), run it; then T-04,
   then T-17 (`frontend/e2e/layout/project.spec.ts`). Also run `e2e/journeys/J2.spec.ts`:
   its board steps (10, 11) must pass, and its `test.fail()` stays until P0-25. Stack:
   `TUMNIS_IMAGE=tumnis:p024 TUMNIS_TEST_PORT=18424 docker compose -p tumnis-p024 -f
   deploy/compose.test.yaml up -d --wait --build`, then from `frontend/`
   `E2E_BASE_URL=http://localhost:18424 npx playwright test e2e/board.spec.ts
   e2e/layout/project.spec.ts e2e/journeys/J2.spec.ts`. Tear down with the same env and
   `down -v`.
2. `git merge main`. As of 2026-09-29, origin/main is still 881a28e (already merged), with
   no P0-19 (`tasks_0002`) and no P0-20. If main has `tasks_0002` after a merge, set
   `down_revision = "tasks_0002"` in
   `backend/tumnis/modules/tasks/migrations/0003_task_changes.py`, and check that
   `cd backend && uv run alembic heads` shows one head per branch. After the merge run
   `make gen` and commit.
3. Recurrence seam: P0-19 builds `GET/PUT/DELETE /v1/tasks/{id}/recurrence` and
   `GET /v1/recurrence?project_id=`. The frontend reads them with hand queries in
   `components/project/queries.ts` (`projectRecurrenceQuery`, `taskRecurrenceQuery`; 404 is
   read as "none") and writes through `apiWrite` in `drawer/RecurrencePicker.tsx`. The shape
   matches P0-19's T-P0-19-19. Once P0-19 is on main, switch to the generated options and
   add the ops to LIVE_MAP (T-P0-22-11 fails on generated ops missing there).
4. Search (P0-20) used a test-helper trash seam. Once both have landed, it should use this
   WP's real `DELETE /v1/tasks/{id}` (trash). Say so in the report.
5. Update Part A (A12) of the plan with the P0-24 names (components, `lib/undo.ts`
   `remember`/`undo`, `lib/task-cache.ts`, knowledge routes, `uiStore` additions).
6. Finish green: `make check` (includes the typecheck); backend unit
   (`uv run pytest -m "not integration and not contract" -n auto`), contract (`-m contract`)
   and integration (`-m integration -n auto`), with `frontend/dist` moved aside first
   (issue #48); Vitest. The full integration layer has NOT been re-run since `5707f3f`,
   `5811d3c` and `fd16624`. The machine is loaded, so rerun timing failures on their own.
   Known flakes: relay timing (#49), readiness under load, `optimistic.test.tsx`.
7. Delete HANDOFF.md in a `chore:` commit.

## Decisions and deviations (report these)

- dnd-kit: `@dnd-kit/react` 0.5.0 injects a `<style>` element during drags, which the
  strict CSP blocks, so this WP uses the plan's fallback `@dnd-kit/core` +
  `@dnd-kit/sortable`. Cards override useSortable's role to `listitem`, with
  roledescription "draggable task".
- No backend `order=due` on `GET /v1/tasks`: `/tasks` follows every cursor and sorts with
  `sortByDue` on the client (query key: the generated op's key plus `pages: "all"`).
- Extra backend routes not in the plan: `DELETE /v1/tasks/{id}` (trash),
  `GET /v1/tasks/{id}/comments`, `GET /v1/projects/{id}/brief`,
  `PATCH /v1/knowledge/documents/{id}`.
- Undo of a create trashes the task.
- T-06 and T-11 move cards through the card's Move menu (same `planMove`, same single
  request); jsdom cannot drag.
- `TaskOut` schema marks defaults required (`5707f3f`); `via` excluded when None
  (`2190474`).
- Vitest `testTimeout` 20 s (`706cb34`).
- Refused-move copy is keyed by the from-status; same-status has its own line.
- Board cards carry an "Open" button; the Tasks view has `j`/`k`, Enter, `s`, `d`.
- Connections edits people, domains and the code path; repo and Coolify links are kept.
- Recurrence PUT sends the task's version for a new rule.
- New (this agent): issue #51, a bug fix to main's code (`truncate_tables`), in its own
  commit on this branch.

## Shared-file edits (report these)

- `frontend/package.json`: the three `@dnd-kit` pins.
- `frontend/e2e/fixtures.ts`: `boardCards`, `openProject`, `recordWrites`.
- `frontend/src/test/factories.ts`: `makeBoard`.
- `frontend/vitest.config.ts`: `testTimeout`.
- `frontend/src/stores/uiStore.ts`, `lib/optimistic.ts`, `components/common/AppShell.tsx`,
  `ConflictToast.tsx` (`brief` entity), `lib/live-map.ts`.
- New (this agent): `backend/tumnis/core/testing_routes.py` (core, issue #51). No edits to
  `pyproject.toml`, `uv.lock`, `Makefile`, `AGENTS.md` or `.importlinter`.

## Gotchas

- rtk filters output: use `rtk proxy uv run pytest ...` to see pytest output.
- Remove a `spec:` marker with the Edit tool on the exact line; `sed -i` was refused. If a
  permission is denied, stop and report it; do not route around it.
- A heredoc or `rm -rf` in Bash trips a "Fact-Forcing Gate" hook: state the facts, or
  write files with the Write tool.
- Prettier reformats a test once its marker goes; `npm run lint` runs `prettier --check .`.
- A label wrapping a `<select>` puts the chosen option into the select's accessible name;
  use `htmlFor`/`id`.
- The MSW unhandled-request strategy is "error": the page must call only endpoints the
  ProjectFake serves.
- Leftover local image `tumnis:p024-main` (main's image used for the repro) can be removed
  with `docker rmi tumnis:p024-main`.

## Verify

```bash
cd frontend && npx vitest run && npm run typecheck && npm run lint
cd ../backend && rtk proxy uv run pytest tumnis/core/tests/integration/test_issue_51_reset_relay_deadlock.py tumnis/core/tests/integration/test_testing_routes.py tumnis/modules/tasks/tests/integration/test_undo.py -m integration
uv run pytest -m "not integration and not contract" -n auto; uv run pytest -m contract; uv run pytest -m integration -n auto
cd .. && make check
```
