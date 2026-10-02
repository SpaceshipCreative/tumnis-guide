# HANDOFF: P4-04 Unattended run windows (continuation c0 -> c1)

Branch `wp/P4-04` (pushed). No PR yet. CodeRabbit: not requested yet. CI: not run on the impl.

## Commits so far
- `df2fe5a8` test(planning): P4-04 spec tests (red): T-P4-04-01..12 + A4.3 (strict xfail / test.fails)
- merge of origin/main at 6ec80f3 (#139 P2-06, agents_0010); clean, no generated-file conflicts
- `feat(planning): unattended window and green-light rules` (this handoff's commit): `planning/rules.py`
  gains Window, window_instance, window_bounds, window_open, Refusal, REFUSAL_WORDS, TaskLite,
  green_light, too_late_to_start, batch_release_at (max(window_end, work_start - 15 min)),
  next_working_start. All 37 unit cases of test_windows.py and test_green_light.py now
  XPASS(strict). Decision 78: remove those markers only after CI shows XPASS, citing the run id.

## Spec tests written (all red, locked once merged)
- unit: `planning/tests/unit/test_windows.py` (T-01, T-02), `test_green_light.py` (T-03, T-04)
- integration: `planning/tests/integration/test_unattended.py` (T-05..08), `test_day_close.py` (T-10),
  `tasks/tests/integration/test_queue_unattended.py` (T-11),
  `notifications/tests/integration/test_overnight_batch.py` (T-09)
- frontend: `frontend/src/components/task/RunUnattendedToggle.test.tsx` (T-12; stub component returns null)
- acceptance: `backend/tests/acceptance/test_a4_3_unattended.py` (two tests). P4-00's phase 4 suite
  was never committed; A4.3 written from the plan text. Say so in the PR body.
- Helpers (not locked, adjust freely): `planning/tests/integration/_unattended.py`,
  `notifications/tests/integration/_overnight.py`, `backend/tests/acceptance/_unattended.py`.
  READ THEM: they fix the API names below.

## Design decided (the tests depend on these names)
tasks (migration tasks_0010 after tasks_0009: `tasks.unattended_queued_at timestamptz`, `unattended_queued_by text`):
- `TaskOut.unattended_queued_at: datetime | None = None` (the drawer toggle reads it; no extra fetch, so
  locked TaskDrawer tests see no new request). Add `unattended_queued_at: null` to the hand-built row in
  `frontend/src/test/msw/project.ts` after `make gen`.
- `queue_unattended(s, actor, task_id, *, queued: bool, now) -> UnattendedOut`; 422 `not_ai` when queueing a
  non-AI (incl. pending) label; unqueue any label = no-op ok; re-queue keeps first queued_at.
- `UnattendedOut {task_id, queued, queued_at, queued_by, may_run_unattended}`; `get_unattended`.
- `may_run_unattended_for(s, task_id)` wraps `rules.may_run_unattended`; `unattended_queue(s)` (live queued
  tasks ordered by queued_at, id, with label/status/tainted/has criteria/project); `consume_unattended(s, task_id)`.
- Routes on tasks router: `GET/PUT /v1/tasks/{task_id}/unattended` body `{queued}`; auth="session",
  idempotent=True (copy an existing session route policy; authz matrix sweeps routes).

planning (migration planning_0004 after planning_0003):
- `unattended_windows(project_id uuid null, weekdays int[], start_local time, end_local time)` via
  create_tenant_table; partial unique indexes: (workspace_id) WHERE project_id IS NULL AND deleted_at IS NULL,
  (workspace_id, project_id) WHERE project_id IS NOT NULL AND deleted_at IS NULL.
- `unattended_runs(run_id, task_id, project_id, window_start, window_end, release_at)`: runs the tick
  started (runs has no `unattended` column; do NOT add an agents migration). Feeds the result hook and the
  `unattended_runs_per_week` metric (join agents.finished_runs, succeeded).
- api: `WindowSpec{weekdays: list[int] 0..6 unique non-empty, start_local, end_local}` (start==end -> 422);
  `UnattendedWindowIn{project_id: UUID|None=None, window: WindowSpec|None, version: int|None=None}`;
  `UnattendedWindowOut{project_id, window, source: project|workspace|none, version}`;
  `get_unattended_window(ctx, project_id=None)`, `put_unattended_window(ctx, body, *, now, session)`
  (versioned: a row exists and version mismatch/None -> 409 stale_version).
  `run_unattended_tick(now)` (all workspaces, in-process; the test tick calls it),
  per-workspace function used by the workflow step; `start` = one transaction: consume flag,
  `agents.request_run(task, RunKind.TASK, unattended=True, ctx=, session=s, now=)`, insert unattended_runs;
  a request_run ProblemError (409 run_already_active / agents_paused...) -> refusal item instead (savepoint).
  Refusal -> review item kind `unattended_refused` (register_review_kind, owner planning, actions
  ("accept","snooze"), impact_scope "task", on_decide accept = unqueue via tasks api), payload
  `{refusal, reason, window_start, batch: "overnight", release_at}`, dedupe_key
  `unattended:{task_id}:{window_start.isoformat()}`, target task, project_id = task's project.
  Skip (stay queued, no item) when `too_late_to_start(now, window_end, policy.max_run_minutes)`.
  Pause: `agents.pause_state_for(s, project_id)`: paused_workspace -> kill_switch, paused_project -> paused.
  Effective window = project override else workspace row. release_at = batch_release_at(window_end,
  next_working_start(window_end, tz, working hours)).
- Result hook: add to agents (additive) `register_result_batch(lookup)`; `accept_result` calls
  `lookup(s, run_id) -> (batch, release_at) | None` and passes `batch`, `release_at` into ResultPayload;
  `ResultPayload` (agents/review_kinds.py) gains `batch: Literal["overnight"] | None = None`,
  `release_at: datetime | None = None`. planning registers the lookup at import (reads unattended_runs).
- day summary: `DaySummaryOut` (api only; NOT rules.DaySummary: T-P1-18-01 compares model_dump) gains
  `queued_unattended: list[QueuedUnattendedOut{task_id, project_id, title, label, queued_at, will_run,
  refusal, reason}]` in queued order; also fill `queued_overnight` with the same tasks' TaskRefs
  (empty stays empty: J7 expects "Nothing queued").
- router (root_router): `GET/PUT /v1/unattended/window?project_id=`.
- workflows: `unattended_tick(scheduled, context)` DBOS workflow, schedule `unattended-tick` `*/5 * * * *`
  in `schedules()` on MAINTENANCE_QUEUE (worker picks it up via register_module_schedules; no worker.py
  edit; deviation from the plan's `"context": None` form, follow planner_tick). Steps: open-window
  workspaces, then one step per workspace.
- `planning/testing.py`: `register_tick("unattended-tick", ...)` calling `api.run_unattended_tick(now)`;
  import it from planning/router.py (focus/testing.py pattern).

notifications (migration notifications_0003 after notifications_0002):
- widen `ck_notifications_decision` to ('now','batch','overnight') NOT VALID (as 0002 did); add
  `release_at timestamptz`; index on (workspace_id, release_at) WHERE decision='overnight' AND released_at IS NULL.
- `record_review_item`: read the item (tasks.get_review_item); payload.batch == "overnight" with release_at
  -> decision "overnight", release_at stored, no `notification.ready`, no push. Always held until a
  release tick at/after release_at (review_item.added's occurred_at is SystemClock time, so never
  compare it). flush() already only takes decision 'batch'.
- rules: `overnight` helpers + `overnight_payload(results, others)` title "1 result from overnight"
  (", N need you" when refusals). Summary row kind "batch", decision "now", details {count, batch:"overnight"}
  so `notify_request` (NotifyBatch) answers it; `_ready` it and start_push.
- workflows: `release_overnight(scheduled, context)` DBOS workflow + schedule `overnight-release` */5;
  `notifications/testing.py` tick `overnight-release` (in-process release; enqueue the push with the
  DBOSClient: workflow_name "notifications.deliver_push", workflow_id push_workflow_id(id), queue PUSH_QUEUE).

frontend: `components/task/RunUnattendedToggle.tsx` (switch "Run unattended", role=switch, PUT route,
AI untainted only; tainted AI -> text "Needs you: from outside content"; others render nothing), place it in
TaskDrawer; workspace window section in Settings (beside WorkingHoursSection, add to settings/sections.ts),
per-project override in `project/rail/ScheduleSection.tsx`; CloseDayPanel "Queued overnight" shows
queued_unattended with "will not run" + reason; review page groups result items with payload.batch
"overnight" under "Overnight". Every new request needs MSW handlers (decision 22). `make gen` for client.

## Remaining steps
1. Migrations (3), models, tasks api+routes, planning api/router/workflows/testing, notifications, agents hook.
2. `make gen`; fix `frontend/src/test/msw/project.ts`; frontend UI; phone width check (375 px).
3. `make check` (semgrep meta test needs SEMGREP_SETTINGS_FILE/LOG_FILE/VERSION_CACHE_PATH under $TMPDIR
   on this VM); unit/contract; integration only your files via CI (Docker load rule).
4. Push, open PR "[P4-04] impl: unattended run windows" (body: deviations below, docs cited: DBOS
   apply_schedules/create_schedule Context7 /dbos-inc/dbos-docs 3.1, hypothesis), comment @coderabbitai review.
5. After CI shows XPASS(strict) per test, remove markers citing the run id (decision 78). Review loop.

## Deviations / Scott items so far
- A4.3 added by P4-04 (P4-00 suite missing). Unattended runs recorded in planning's own table instead
  of a runs.unattended column (avoids an agents migration). Additive agents edit (ResultPayload fields +
  result-batch hook). Overnight hold is a new notifications decision value.
- `batch_release_at` clamps to the window end. Plan's DONE item "a real overnight run on the homelab" is Scott's.

## Verify
- `cd backend && rtk proxy uv run pytest -q tumnis/modules/planning/tests/unit -k "windows or green_light"`
- `cd backend && uv run alembic heads`
