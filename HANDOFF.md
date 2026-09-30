# HANDOFF: P2-04 (dispatch_run and the run view), continuation c2 next

Branch `wp/P2-04` (pushed to `origin/wp/P2-04`). Based on main at 7083652 (#104, P2-02); main
had not moved at c1. No PR is open yet. No CodeRabbit review, no CI run yet. Scratch folder
for the next agent: `$TMPDIR/P2-04-c2/`.

## Commits

| Commit | What |
| --- | --- |
| 38f6010 | c0: `test(agents): P2-04 backend spec tests (red, work in progress)`: 17 backend spec tests + `_runs.py` helpers |
| (this commit) | c1: `chore: P2-04 handoff (work in progress)`: rules, migrations, api, payloads, workflows, frontend red specs |

The c1 commit is WORK IN PROGRESS and was made on a HANDOFF NOW: `ruff check`, `ruff format`,
`mypy tumnis` and `lint-imports` were clean just before it, but the new workflow code has
never run, and the subscribers/routes below are missing, so the integration specs are still
red. `make check` unit layer: 1508 passed, 1 failed (`test_semgrep_rules_pass_their_own_tests`,
the known ~/.semgrep read-only issue: set SEMGREP_SETTINGS_FILE, SEMGREP_LOG_FILE and
SEMGREP_VERSION_CACHE_PATH under $TMPDIR). That run was before workflows.py grew; rerun it.

## Done in c1 (uncommitted work now in the c1 commit)

- `agents/rules.py`: `RUN_TRANSITIONS`, `ACTIVE_RUN_STATUSES`, `TransitionNotAllowed`,
  `run_transition`, `active_seconds`, `over_ceiling(started_at, now, ceiling)`,
  `DispatchTask`, `DispatchProfile`, `Refusal`, `can_dispatch`. T-03, T-17, T-19 GREEN;
  their xfail markers are removed. In test_run_rules.py the first loop variable was renamed
  `label` -> `any_label` (mypy redefinition error; no assertion changed; the file is new in
  this branch, so spec-guard does not lock it).
- `core/limits.py`: `WAIT_SLICE_S`, `RUN_WALL_CLOCK_CEILING`.
- Migrations (by a helper subagent; squawk passes offline):
  - `agents_0005` (`agents/migrations/0005_runs_v2.py`): runs.state_seq, active_seconds_used,
    runner_id, stop_reason, packet, rerun_of, tasks_created; run_events.seq (sequence
    `run_events_seq_seq`, backfilled with row_number, nullable because squawk refuses SET NOT
    NULL; unique `ux_run_events_ws_run_seq`); partial unique `ux_runs_ws_task_kind_active`.
  - `tasks_0007` (`tasks/migrations/0007_results.py`): `results` tenant table; task_id FK to
    tasks ON DELETE CASCADE (purge takes results with the task); unique (ws, run_id).
  - models mirrored (`agents/models.py`, `tasks/models.py` `Result`); `row_factory.COLUMN_VALUES`
    has `("results", "outcome"): "done"`. App role gets sequence USAGE via
    deploy/postgres/initdb/02-database.sql default privileges (no GRANT needed).
- `agents/payloads.py` (new): RunRequestedV1, RunSignalV1, RunStartedV1, RunFinishedV1.
  `tasks/payloads.py`: ResultPostedV1. (Contract fixtures + `make gen` NOT done yet.)
- `tasks/api.py`: FileTouched, ResultLink, ResultFields, ResultOut, `result_of_run`,
  `post_result(s, actor, task_id, run_id, fields, now=) -> (ResultOut, created)`.
- `agents/review_kinds.py`: kinds `result` (accept/reject/snooze, reject payload
  `RejectFeedback{feedback}`) and `run_limit` (accept/snooze); ResultPayload, RunLimitPayload.
- `agents/api.py` (end of file, "Runs" section): LIVE_RUN, constants, `configure_runs`,
  `run_caps`, RunRequested, RunRequestIn, RunOut, RunEventOut, RunEventsPage, `request_run`
  (session= optional; savepoint; IntegrityError -> 409 run_already_active), `get_run`,
  `run_project` (+ `register_project_lookup("runs", ...)`), `run_events_page`, `log_text`
  (scrub + 8 KiB cut), `add_system_line`, `finish_run_in` (idempotent end: row, revoke +
  redact tokens, closing line, run.finished, run_limit item), `signal_run`, `cancel_run`,
  `PostResultIn`, `ResultOut`, `accept_result(s, actor, caller_run, inp, now=)`.
- `agents/workflows.py` ("dispatch_run" section): Prepared, RunHandleData, `prepare_run`,
  `send_to_agent` (build_packet + `_with_token` + adapter dispatch + killpoint
  `agents.send_to_agent.after_dispatch` + stores redacted packet), `now_s`, `stop_agent`,
  `finish_run`, `park`, `bump_state`, `_end`, `_signal` (legacy message shapes), `_supervise`
  (killpoint `agents.dispatch_run.waiting_recv`), `dispatch_run(workspace_id, run_id)`
  (killpoint `agents.dispatch_run.after_send`), `start_dispatch` (SetWorkflowID(run id),
  partition key = project id, priority, fresh-context create_task), `deliver_signal`.
  NOTE: the workflow takes `(workspace_id, run_id)`, not the plan's `(run_id)`: steps need
  the workspace for tenant_session. P2-09's check_pause/held is not built (P2-09's scope).
- `_runs.py` helper: `relay()` imports `tumnis.wiring` instead of `tumnis.modules.tasks.events`
  (import-linter modules-api-only).
- Frontend red specs (`test.fails`, confirmed red, typecheck + eslint clean):
  `frontend/src/components/runs/RunView.test.tsx` (T-15; RunView({runId}); log role "log"
  named "Run log"; events page body `{items, next_after_seq}`; Stop button; "Elapsed" label)
  and `frontend/src/components/review/ResultItem.test.tsx` (T-16; ResultItem({item,
  onDecide}); Reject disabled until "Feedback" typed; Enter on the card accepts).

## Remaining steps (in order)

1. `agents/events.py`: subscribers `agents.start_dispatch` on `run.requested` (call
   `_workflows.start_dispatch(envelope.workspace_id, run_id, project_id, priority)`) and
   `agents.deliver_run_signal` on `run.signal` (`_workflows.deliver_signal(ws, run_id, kind,
   reason, key=str(envelope.event_id))`). Extend the ONE `agents.apply_review_decision`
   (decision 29): kind `result` accept -> `tasks.change_status(s, ActorRef(envelope.actor),
   task, DONE, version)` if task is in_review; reject -> in ONE tenant_session with the
   envelope actor: `tasks.add_comment(feedback)`, change_status R->P, then
   `api.request_run(task, TASK, rerun_of=<item payload run_id>, ctx=, session=s)`; skip all
   if the task is no longer in_review (idempotent redelivery). The item payload (run_id) is
   read with `tasks.get_review_item`. Update the events.py docstring.
2. `agents/ws.py` `_result`: when `runs.workflow_id == str(run_id)` (a dispatch_run run):
   keep the `result` run event; status succeeded -> parse `output_json` leniently into
   `api.PostResultIn(run_id=..., **output)` and call `api.accept_result(s, self.ctx.actor,
   run_id, inp, now=self.now())` in the same tenant_session (catch ProblemError: log, ack);
   invalid output or other status -> `api.signal_run(..., "agent_failed", reason=...)` (a
   `cancelled` result after our own stop may simply be recorded). NEVER `hub.client().send`
   for these (the kill tests have no DBOS in the test process). Keep the old send for
   `run_skill:` ids. Also route stream `text` through `api.log_text` in `_insert_event`.
3. `workflows.sweep_workspace_step`: for runs whose workflow_id == str(run id) (running or
   waiting_on_human on an offline runner) emit `RunSignalV1(kind="runner_lost")` in the
   sweep transaction instead of flipping the row; keep the old flip + send for run_skill runs
   (P1-04 test_runner_sweep.py). `runner_sweep` must not send for those.
4. `agents/mcp.py`: op `post_result` (scope tasks:write, write=True, input
   `PostResultToolIn(WriteInput, PostResultIn)`, rest POST `/v1/runs/{run_id}/result`,
   project_resolver = api.run_project on raw run_id, `session_twin_allowed=False`, handler
   -> `api.accept_result(call.session, call.actor, call.caller.run_id, data, now=call.now)`).
   Remove `"post_result"` from `PENDING_TOOLS` in `core/agent_surface.py`.
5. `agents/router.py`: `POST /v1/tasks/{task_id}/run` (session, idempotent, 202,
   `api.RunRequested`; call request_run with the route's SessionDep and principal ctx);
   `GET /v1/runs/{id}` (session_or_key tasks:read, project_param lookup:runs);
   `GET /v1/runs/{id}/events?after_seq=&limit=` (RunEventsPage; not `Page`, so the route
   registry is fine with a model); `POST /v1/runs/{id}/cancel` (202, idempotent);
   `POST /v1/runs/{run_id}/result` (the twin; policy session_or_key, tasks:write,
   idempotent, project_param lookup:runs; raw = {**body, "run_id": run_id}).
6. `worker.py`: keep `DBOS.register_queue(RUNS_QUEUE, partition_concurrency=2)`, add
   `polling_interval_sec=0.5` (T-01 timing). Consider `configure_runs` settings later (PR2).
7. Contract fixtures `backend/tests/contract/fixtures/events/{run.requested,run.signal,
   run.started,run.finished,result.posted}/v1.json`, then `make gen` (schemas, openapi,
   generated tests, openapi-ts client). Then `frontend/src/lib/live-map.ts`: add a `"run"`
   LiveEntity with details `agentsGetRun`, `agentsListRunEvents` (or whatever op ids gen
   produces) so T-P0-22-11 passes.
8. Run: `make check`; `make test` (bare); `make test-int` (bare; VM loaded, CI is the
   judge). Remove each P2-04 xfail marker only after that test passed. Kill tests: plan asks
   20 runs in a row (CI or local loop).
9. Open PR1 (`[P2-04] impl: dispatch_run and the run view`), body with deviations below,
   `@coderabbitai review` once, review loop, MERGE-READY to main.
10. PR2 `wp/P2-04-impl-2`: RunView, ResultItem, useRunEvents, `run` search param on
   projects.$projectId, signals.py refactor, `supervise_run`, `reconcile_runs` (hourly on
   maintenance), Playwright runner-fake routes, settings wiring for configure_runs.

## Decisions settled (binding for c2; c0's list still applies)

c0 decisions 1-15 (see git history of this file, commit a4ef89b) still hold. Additions:
- `finish_run_in` is the single end path (workflow step and cancel of a queued run).
- The `result` review item is added by `agents.accept_result` (the owner of the kind), right
  after `tasks.post_result`, in the same transaction; the plan's wording puts it inside
  `tasks.api.post_result`. PR-body deviation.
- Closing system log lines: time_limit "Stopped at the time limit", wall_clock_ceiling
  "Stopped at the wall-clock limit", runner_lost "Stopped: the runner stopped answering",
  stopped_by_user "Stopped by you"; none on success.
- request_run refusals: 422 label_not_runnable; 409 status_not_runnable, no_ready_profile,
  run_already_active.

## PR-body deviations to record

- No deduplication_id on the partitioned `runs` queue (DBOS 3.1.0 refuses it; Context7
  /dbos-inc/dbos-docs prompting.md: "setting any partition_* limit makes the queue
  partitioned ... disabling deduplication"); deterministic workflow id + partial unique index.
- No `priority_enabled` flag in DBOS 3.1.0 `register_queue`; priority via
  `SetEnqueueOptions(priority=)` (queue tutorial: lower = higher).
- `over_ceiling` takes the ceiling as a parameter (rules purity).
- `dispatch_run(workspace_id, run_id)` signature; P2-09 pause/held not built.
- `result` review item added in agents.accept_result.
- `run_events.seq` is one global sequence: order is insertion order; a concurrent insert
  can commit out of seq order (rare: one socket per runner).
- `results.task_id` FK ON DELETE CASCADE.
- `result/task_result/1` schema not registered (no spec test needs it); decide and state.

## Scott / coordinator items

- A2.1 / `frontend/e2e/journeys/J3.spec.ts` is P2-00's (wp/P2-00-spec); after it merges,
  merge main and un-fail A2.1 only if it passes.
- R-29's 24-hour ceiling remains a plan default flagged for Scott.
- Open item unchanged: who mints a profile's API key (keyless profiles dispatch without a token).
- Watch: T-09 posts a second result right after the first; if the run ends first, the
  revoked token could 401 the second post (race). Report, never weaken.

## Verify commands

```bash
cd backend && uv run pytest -q -p no:randomly -m "not integration and not contract" tumnis/modules/agents/tests/unit/test_run_rules.py
cd backend && uv run ruff check tumnis && uv run mypy tumnis && uv run lint-imports
make check          # from the worktree root
make test           # bare, unit + contract
make test-int       # bare, integration
```

Push with `/usr/bin/git push origin HEAD:wp/P2-04`. Use `/usr/bin/git`, never plain `git`.
Never merge.
