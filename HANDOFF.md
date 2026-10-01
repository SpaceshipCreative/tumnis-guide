# P2-15 handoff (Focus events engine and focus bar)

Branch `wp/P2-15` (pushed as `HEAD:wp/P2-15`; local branch renamed to `wp/P2-15`). No PR yet.
Push with `/usr/bin/git push origin HEAD:wp/P2-15`. Scratch: `$TMPDIR/P2-15-c0/` (= `/tmp/claude-1002/P2-15-c0/`).

## Commits so far

- `6bfbf7f` test(focus): P2-15 spec tests (red): T-01..15 and T-18, strict xfail, with stubs.
- `feat(focus): the focus rules ...`: rules.py complete, T-01..07 unmarked and green, 100% coverage
  (`uv run pytest tumnis/modules/focus/tests/unit --cov=tumnis.modules.focus.rules --cov-branch`).
- merge of origin/main (brings #124; no conflicts, nothing generated to redo yet).
- `feat(focus): focus tables (focus_0001)`: migration `backend/tumnis/modules/focus/migrations/0001_focus.py`
  (revision `focus_0001`, branch `focus`, depends_on `auth_0001`) and `models.py`. Not yet run.
- this handoff.

## Spec tests

- Green: T-P2-15-01..07 (unit, `tumnis/modules/focus/tests/unit/test_focus_rules.py`) + edge tests
  `test_focus_rules_edges.py`.
- Red (strict xfail, `spec:P2-15`): T-08/09/10 `test_focus_workflows.py`, T-11/12
  `test_focus_session_durable.py`, T-13 `test_focus_gate.py`, T-14/15 `test_focus_attribution.py`,
  T-18 `test_focus_digest.py`. Harness: `tests/integration/_focus.py` (helpers, no assertions,
  may change) and `conftest.py` (fixture `focus`; resets `workflows.use()` and decisions providers).
- T-18 needs #103 (P2-03 digests, open). When #103 merges: add `"focus.responded"` to
  `DIGEST_EVENTS` in `agents/rules.py` (#103 says "P2-15 adds focus.responded"), then unmark.
- Frontend T-16/T-17 belong to impl-2 (`wp/P2-15-impl-2`), not written yet.
- A2.6 (`frontend/e2e/journeys/J8.spec.ts`) cannot pass: no seed `Write proposal`, no runner
  recording `plan__write_proposal_block.result.json` (seed gap is with Scott). Leave `test.fail()`.

## Design (binding for what is left; agreed with the advisor)

Time: focus workflows NEVER read a clock. Each loop iteration's `now` comes from: the start
instant passed by the subscriber (event `occurred_at`) on the first iteration; a `tick` message's
`now`; an `end`/`response`/`wake` message's `at`; or, on a recv timeout, the due instant (timeout
=> due reached; if the wait was capped by WAIT_SLICE_S=3600 then `now += waited`). This makes the
in-process tests, the subprocess kill test (T-11) and fakes e2e all work. `workflows.use(min_due_seconds=...)`
caps every wait (R-30 real-time proof in T-12; counts as due reached).

Files still to write:
- `focus/payloads.py`: `focus.event` FocusEventV1 {event_id, kind, task_id?, rule, level, message, fired_at};
  `focus.responded` FocusRespondedV1 {event_id, task_id?, session_id?, response}; `focus.level_changed`
  FocusLevelChangedV1 {from_ (alias "from", copy TaskStatusChangedV1's model_config), to, scope: "workspace"|"today"}.
  #103 reads exactly `{task_id, response}` and `{from, to, scope}`. Add fixtures
  `backend/tests/contract/fixtures/events/<name>/v1.json` and run `make gen`.
- `focus/api.py`: settings section `focus` = FocusSettings(level="quiet" default per FR-10.1,
  activity_signals=True); `current(ctx, now)` -> {level (effective), workspace_level, override_level,
  session {id, task_id, title, started_at, cadence_min, doubled, next_check_in_at}, messages (today's
  focus_events oldest first: id, kind, task_id, level, rule, message, fired_at, response)};
  `set_level` (put_setting in the request session + emit level_changed scope workspace);
  `respond(event_id, response, to_task_id?)` (insert focus_responses; apply_response on the open
  session row; `stuck` fires a `stuck` event when the level fires it; emit focus.responded);
  `less_of_this(event_id?)` (override for local today = lower(effective); response row; emit
  level_changed scope today + focus.responded). day close = next local midnight
  (`tasks.rules_recurrence.next_day_close` semantics; compute in focus). tz from
  `auth.api.get_workspace_settings(ctx).timezone`; day_end from `planning.api.get_working_hours`
  + `core.clock.local_to_utc` (weekday without hours: no day_end).
  `fire_event(s, kind, level, task_id, session_id, plan_id, at, detail, message, dedupe_key)`:
  INSERT .. ON CONFLICT (workspace_id, dedupe_key) DO NOTHING RETURNING id; if new: emit
  focus.event + `live.mark_changed(s, "focus", id)`. Messages: block_start "Time for {title}. First
  step: {first_action}"; rule via `rules.attribution` (detail only for check_in_due: "25 min cadence").
  Worker-side: `start_session(task_id, at)` (level >= nudge; cadence = project focus_cadence_min or 25;
  other open sessions ended and `switched` fired for the new task at Coach+; unique open session per
  task, ON CONFLICT reuse), `end_sessions(task_id, at)`, `next_due(session_id, now)`,
  `check_in(session_id, now)` (read state, compute due, activity = last_activity_at +
  `agents.api.task_activity_times`, ask the Noul OUTSIDE the locking transaction, then lock and
  re-check `last_check_at`, then fire or suppress; suppression moves last_check_at to now; only
  fire while the task is still in_progress), `fire_planned(plan_id, ev, now)` (level at now,
  not_started only while not in_progress, gate gateable kinds, fired_at = ev.at;
  dedupe `plan:<plan>:<kind>:<task>` and `day_end:<day>`), `record_activity(task_id, at)`.
- `focus/workflows.py`: queue `focus`, topic `focus`, `WAIT_SLICE_S = 3600`; workflow ids
  `focus_plan:<ws>:<day>:<plan_id>` (deviation: ws and day added so plan.published can send
  `superseded` by prefix) and `focus_session:<task_id>:<started_at iso>`; killpoint
  `focus.session.waiting` just before the session's recv. Start workflows from subscribers with
  `asyncio.get_running_loop().create_task(..., context=contextvars.Context())` + `SetWorkflowID`
  + `DBOS.enqueue_workflow_async("focus", ...)` (copy `agents.workflows.start_dispatch`). focus_plan
  skips events before its start instant.
- `focus/events.py` subscribers: plan.published (start focus_plan, supersede older ones of the day),
  task.status_changed (start/end sessions, send `end` with idempotency_key=event_id),
  run.started + artifact.updated (record activity; artifact owners via
  `integrations.api.context_owners(target_type="artifact", owner_type="task")`), focus.responded
  (send `response` to the session), focus.level_changed (send `wake` to open sessions).
  calendar.synced: not added (nothing on main moves a published plan's blocks on a sync) - deviation.
- `focus/testing.py` `wake(client, now)`: `client.list_workflows_async(name=["focus_plan","focus_session"],
  status="PENDING", load_input=False, load_output=False)` then `send_async(id, {"kind":"tick","now":iso}, "focus")`.
  Register tick `focus-wake` in a new tiny registry in `core/testing_routes.py`:
  `POST /v1/test/tick/{schedule_name}` (auth none, idempotent=False + reason, 404 `unknown_tick`),
  calling `wake(deadletter.dbos_client(), request.app.state.clock.now())`. Import focus.testing from
  focus/router.py so the api registers it. Add `focus-wake` to Part A of the plan.
- `focus/router.py`: GET /v1/focus/current, POST /v1/focus/respond, PUT /v1/focus/level, POST /v1/focus/less;
  `RoutePolicy(auth="session", idempotent=True)` for writes, `SessionDep`, clock from `request.app.state.clock`.
- `decisions/questions/nudge_warranted.py` + `decisions.api.ask_nudge_warranted(...)`: mirror
  `approval_need.py` / `ask_approval_need`. Catalog fields are locked: level, event_kind,
  minutes_into_block (minutes since start: keeps the 24 h decision cache from reusing T-13's
  first answer), task_status, recent_responses, minutes_since_last_nudge. Main question `nudge`.
  Suppress threshold = 1 - 2*t_no (t_no 0.20 -> 0.6; fallback stricter). SubjectRef type
  "focus_session" exists.
- Shared edits still to make (keep additive, list in PR): `worker.py` (`register_queue("focus")`
  and add to `main_queues()`; test_worker_listen checks both), `agents/api.py` (append
  `task_activity_times(ctx, task_id, since, until)` from run_events joined to runs, at END of file),
  `projects/api.py` (`focus_cadence_min` on ProjectOut/ProjectPatch + `focus_cadence(project_id)`),
  `core/testing_routes.py` (tick registry), `core/tests/integration/row_factory.py` COLUMN_VALUES for
  focus_sessions.level, focus_events.kind/level, focus_responses.response, focus_overrides.level,
  `frontend/src/lib/live-map.ts` (`focus` entity, lists `focusGetCurrent`: T-P0-22-11 sweeps ops),
  generated files via `make gen` (never hand-edit).

## Verify

- `make check` (semgrep meta test needs `SEMGREP_SETTINGS_FILE`, `SEMGREP_LOG_FILE`,
  `SEMGREP_VERSION_CACHE_PATH` under `/tmp/claude-1002/P2-15-c0/semgrep/`).
- Unit: `cd backend && uv run pytest -q -m "not integration and not contract" -n 3 tumnis/modules/focus`.
- Integration: bare `make test-int` once, else CI.

## Scott items

- A2.6 blocked by the seed gap (seed `Write proposal` and runner recording `plan__write_proposal_block`).
- T-18 waits for #103.
- `NoulAnswer` is focus's own dataclass (rules may not import agents.rules); Noul fields follow the
  locked catalogue, not the plan prose.
- `focus_min_due_seconds` is a module seam (`workflows.use`), not a Settings field.
- New core test route `POST /v1/test/tick/{schedule_name}` (no WP had built it).
