# P0-19 handoff: Recurrence, rollover and day close

Worktree `/Users/sjordan/Projects/Tumnis-Guide-wt/p0-19`, branch `wp/P0-19`. main was merged in at `d438482` (PR #40, the P0-23 follow-up).

## Done (commits)

| SHA | What |
| --- | --- |
| `6757671` | `test(tasks): P0-19 spec tests (red)`: T-P0-19-01 to 19, `tasks/tests/fixtures/dst_vectors.yaml`, conftest helpers `tz_workspace` and `drain_workflows`, `WorkerKiller.start_worker/stop_worker/dbos_client`, interface stubs |
| `71837ea` | `feat(tasks): recurrence and day-close rules`: `rules_recurrence.py` (100% line and branch coverage), extra `tests/unit/test_recurrence_rules.py` |
| `0c4b238` | `feat(tasks): recurrence_rules and day_closes tables`: revision `tasks_0002`, models, `auth.api.workspace_timezone`, `core.idempotency.delete_expired` |
| `d438482` | merge of main (conflict in tasks/api.py imports only) |
| `4cc1a22` | `feat(tasks): recurrence api, day close and housekeeping workflows`: api section, routes, workflows, worker schedules, `wiring.load_workflows`, row_factory value |

## Spec tests

All 19 are green, with the markers removed (unit 01 to 09, 11, 12; integration 10, 13 to 19, run once each). Nothing is red.

## Remaining steps, in order

1. Run the full layers from `backend/`: `uv run pytest -m "not integration and not contract" -n auto`, `-m contract`, `-m integration -n auto` (move `frontend/dist` aside first if it exists). Watch these:
   - the meta sweeps: route registry, authz matrix (the new routes use `lookup:tasks` and `query:project_id`, which already have targets), table registry, isolation (`two_workspaces`/`minimal_row` for `recurrence_rules` and `day_closes`), and a models-vs-migration check if there is one;
   - schemathesis over the new routes;
   - squawk on `tasks_0002` (`scripts/ci/squawk_migrations.py`). It drops and re-adds the `task_comments`/`task_context_items` foreign keys NOT VALID with ON DELETE CASCADE, and adds `fk_tasks_recurrence_rule_id_recurrence_rules` NOT VALID.
   - The known flake `core/tests/integration/test_relay.py::test_notify_wakes_relay_before_poll`: rerun it alone before blaming a change.
2. Run the kill test 20 times: `for i in $(seq 20); do uv run pytest tumnis/modules/tasks/tests/integration/test_housekeeping.py -k worker_kill -m integration -q -p no:randomly || break; done`.
3. `npm ci` in `frontend/` (node_modules is absent in this worktree), then `make gen` and commit the output (openapi.json, the generated client, the contract tests). The new GET operations are `tasksGetRecurrence` and `tasksListRecurrence`. Add both to `LIVE_MAP` (entity `task`: details `tasksGetRecurrence`, lists `tasksListRecurrence`) or to `NOT_LIVE` in `frontend/src/lib/live-map.ts`. Run `npm --prefix frontend run typecheck` (the coordinator asked for this: `make check` does not run `tsc` yet) and the Vitest suite.
4. Check that schedules are registered once per worker start (`DBOS.list_schedules()`): `apply_schedules` upserts by name. A quick integration test is optional.
5. Seed (plan "Fixtures, fakes and data", optional): one weekly recurring task, "Friday invoice run", so previews show the Schedule rail. This needs a `recurrence` field on `tumnis.seed.TaskSeed` and seed-writer support. Check the seed-count assertions before adding a task. It is not done yet; if you skip it, say so in the report.
6. Add P0-19's names to Part A, A12, of `docs/IMPLEMENTATION-PLAN-DETAILED.md` (definition of done): revision `tasks_0002`, tables `recurrence_rules` (columns `latest_occurrence_at`, `next_due_at`, `weekday`, `month_day`, `due_time`, `project_id`) and `day_closes(day, closed_at, rolled_over)`, `auth.api.{WorkspaceTimezone, workspace_timezone}`, `core.idempotency.delete_expired`, `wiring.load_workflows`, `worker.register_task_schedules`, the `WorkerKiller` methods, the conftest helpers `tz_workspace` and `drain_workflows`, the api names (`RecurrenceIn`, `RecurrenceOut`, `TaskRecurrenceOut`, `get/put/delete/list_recurrence`, `create_due_successors`, `day_close_facts`, `DayCloseFacts`, `roll_over_today`, `purge_trash`), the kill point `tasks.roll_over_today`, and the schedule names `day-close-tick` and `housekeeping`.
7. Run `make check`, commit, then send the final report (short: commits, per-layer results, shared-file edits, deviations).

## Coordinator notes (binding)

- **Alembic.** P0-24 adds `tasks_0003` (`task_changes`), also chained after `tasks_0001`. Keep `tasks_0002` after `tasks_0001`. If P0-24 is on main when you run `git merge main` before finishing, change `down_revision` in `backend/tumnis/modules/tasks/migrations/0002_recurrence.py` to `tasks_0003`, so the tasks branch has one head (check with `uv run alembic heads`).
- **Recurrence routes.** P0-24's frontend calls these routes and aligns to them. Keep the paths and shapes as built: `GET/PUT/DELETE /v1/tasks/{id}/recurrence` (PUT body `{preset|cron, weekday?, month_day?, due_time?, version}`, DELETE `?version=`, 204) and `GET /v1/recurrence?project_id=` (`Page[RecurrenceOut]`).
- Main moved (PR #40): `list_tasks` returns `TaskPage` and takes `order`. It is merged already. Before finishing, merge main again: keep both sides, take theirs for generated files, then run `make gen`.

## Decisions and deviations

- **No croniter, and `local_to_utc` is not imported, in `rules_recurrence.py`.** The locked meta-test T-P0-01-09 allows only pure stdlib, pydantic and `tumnis.core.types` in `rules*.py`. So the rules carry a small 5-field cron parser (numbers, `*`, ranges, steps, lists; day-of-week 0 or 7 is Sunday; Vixie OR semantics when both day fields are restricted) and a private `_local_to_utc`, identical to `core.clock.local_to_utc`. T-P0-19-03 asserts that they agree. No dependency was added (DBOS vendors its own croniter).
- **The timezone anchor** is `workspaces.updated_at`, read through the new `auth.api.workspace_timezone` (the plan says "the settings row's updated_at"). Any settings PUT moves it (a threshold change too), which can only delay a missed close. A fresh workspace anchors at its creation time.
- **Time arguments.** `close_day(workspace_id, day, now=None)` and `recurrence_tick(workspace_id, now=None)` take an optional `now`. The tick passes its scheduled time; when `now` is left out, a `current_time` step reads the module clock. `roll_over_today(workspace_id, day, now)` is the step. The api function `api.roll_over_today(s, day, now)` inserts the `day_closes` row first (ON CONFLICT DO NOTHING) and rolls nothing when the day is already closed; the plan inserts it last. This order makes a repeat a no-op.
- **Rule columns.** `recurrence_rules` also stores `latest_occurrence_at` (the latest instance's UTC instant, used as `latest` for `successor`) and `project_id`, `weekday`, `month_day` and `due_time`.
- **Successor on done** runs inside `change_status`'s transaction: a small hook at the end of `_transition` calls `_successor_on_done`. Rule rows are not locked. The unique index with ON CONFLICT DO NOTHING arbitrates, as the plan says; locking the rule row would deadlock against `_slot`'s per-project advisory lock.
- **PUT semantics.** The task is the first instance when it is the rule's latest (or when it has no rule). Its occurrence is the first one on or after its due date (or after now), and its `due_on` is set to that local date. A PUT on an older instance changes only the spec.
- **DELETE** soft-deletes the rule and clears the task's `recurrence_rule_id` and `occurrence_on`. Other instances keep their links.
- A trashed latest instance counts as open for the tick (so the series continues). Trash purge deletes children first and keeps a parent whose subtask stays.
- `worker.register_task_schedules` imports the tasks workflows by name (`importlib`), as `app.module_routers` does, because a static import broke import-linter's modules-api-only contract (the chain auth tests -> cli -> worker).
- `tasks/api.py` changes are additive: the helper `_announce_created` was extracted from `_insert`, the done hook was added in `_transition`, and a new section was appended at the end.

## Shared-file edits so far

- `backend/tests/fixtures/__init__.py`: `WorkerKiller.start_worker`, `stop_worker`, `dbos_client` (additive).
- `backend/tumnis/core/tests/integration/row_factory.py`: `("recurrence_rules", "preset"): "daily"`.
- `backend/tumnis/core/idempotency.py`: `delete_expired`.
- `backend/tumnis/modules/auth/api.py`: `WorkspaceTimezone`, `workspace_timezone`.
- `backend/tumnis/wiring.py`: `load_workflows()`.
- `backend/tumnis/worker.py`: `register_task_schedules()`.
- Not touched: `pyproject.toml`, `uv.lock`, `Makefile`, `AGENTS.md`, `.importlinter` and `frontend/package.json`.

## Gotchas

- The rtk hook filters pytest output. Use `rtk proxy uv run pytest ...` to see results.
- The fixture workspace's `updated_at` is real DB time, which is later than the test clock. Tests set the anchor with `tz_workspace(tz, at=...)`, or through a settings PUT at clock time.
- `drain_workflows()` polls `DBOS.list_workflows_async(status=["ENQUEUED","PENDING"])` after a tick.
- The kill test enqueues `close_day` through `DBOSClient` with workflow ID `day_close:{ws}:{day}` and retries until the worker has created the system tables.

## Verify

```bash
cd backend
uv run pytest tumnis/modules/tasks/tests/unit -k "recurrence or successor or day_close" -q
rtk proxy uv run pytest tumnis/modules/tasks/tests/integration/test_housekeeping.py tumnis/modules/tasks/tests/integration/test_recurrence_flow.py -m integration -q
uv run pytest tumnis/modules/tasks/tests/unit -q --cov=tumnis.modules.tasks.rules_recurrence --cov-branch --cov-report=term-missing -k "recurrence or successor or day_close"
cd .. && make check
```
