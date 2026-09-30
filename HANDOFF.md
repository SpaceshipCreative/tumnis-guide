# HANDOFF: P2-04 (dispatch_run and the run view)

Branch `wp/P2-04` (pushed to `origin/wp/P2-04`). Based on main at 7083652 (#104, P2-02).
No PR is open yet. No CodeRabbit review, no CI run yet. Scratch folder: `$TMPDIR/P2-04-c0/`.

## Done

| Commit | What |
| --- | --- |
| 38f6010 | `test(agents): P2-04 backend spec tests (red, work in progress)`: 17 backend spec tests + `_runs.py` helpers |

Spec tests written (all `xfail(strict=True, reason="spec:P2-04")`, T-ID first docstring line, `req`/`wp` marks):

- `backend/tumnis/modules/agents/tests/unit/test_run_rules.py`: T-03 `test_active_seconds_excludes_waiting`, T-19 `test_can_dispatch_stuck_any_label`, T-17 `test_run_transition_table`. Confirmed red (3 xfailed).
- `.../integration/test_dispatch_concurrency.py`: T-01, T-02, T-14.
- `.../integration/test_dispatch_timeout.py`: T-04, T-18.
- `.../integration/test_dispatch_failure.py`: T-08.
- `.../integration/test_results.py`: T-09, T-10, T-11, T-12.
- `.../integration/test_run_events.py`: T-13.
- `.../integration/test_dispatch_kill.py`: T-05/06/07 as one parametrized test (`[after_send]`, `[inside_send]`, `[waiting_recv]`).
- `.../integration/_runs.py`: helpers (no assertions): `run_world` (project "Acme site" + profile on a protocol-2 fake runner, optional API key so dispatches carry a token), `RunWorld.ai_task/request/packets/token`, `relay(db)` (runs `events.relay_forever` in the test after marking the set-up's outbox rows sent, so project.created never provisions a second profile), `finish` (protocol-2 `result` frame), `stream`, `cancels`, `wait_until`, `owner_rows`, `workflow_status`.

The 14 integration tests collect but have NOT been run (Docker). mypy on the tests currently fails only on names the implementation adds (listed below); that is expected for the red state. `make check` will not pass until they exist, so either implement first or commit the rest of the red set with the implementation stubs.

## Remaining steps (in order)

1. Vitest red specs (`test.fails`, dynamic `await import(...)` inside the body so a missing module fails the test, not the file):
   - T-15 `frontend/src/components/runs/RunView.test.tsx`: `[P2-04][FR-5.5] streams lines in order and Stop cancels` (lines appended from paged fetches; Stop posts `/v1/runs/{id}/cancel`; elapsed ticks with fake timers).
   - T-16 `frontend/src/components/review/ResultItem.test.tsx`: `[P2-04][FR-5.8] reject requires feedback` (Reject disabled until feedback typed; accept is one keystroke).
   Then amend nothing; new commit `test(frontend): P2-04 spec tests (red)`.
2. Implement backend per the plan's TDD sequence (docs/IMPLEMENTATION-PLAN-DETAILED.md lines 12364-12670), removing one marker at a time (Scott approved marker removal).
3. PR1 = `wp/P2-04`, title `[P2-04] impl: dispatch_run and the run view` (backend TDD steps 1-8). PR2 = `wp/P2-04-impl-2` based on `wp/P2-04` (frontend T-15/16, `signals.py` refactor, `supervise_run`, `reconcile_runs`, Playwright runner-fake routes). Same pattern as P2-01 (#81 / #84).
4. `make gen` after new events/routes; contract fixtures under `backend/tests/contract/fixtures/events/<name>/v1.json` for `run.requested`, `run.signal`, `run.started`, `run.finished`, `result.posted` (see how P2-13 added `artifact.updated`).
5. PR body, CodeRabbit once, review loop, MERGE-READY message to `main`.

## Names the tests expect (implement these)

- `agents.rules`: `RUN_TRANSITIONS` (plan table), `run_transition(current, target)` raising `TransitionNotAllowed`, `active_seconds(spans)`, `over_ceiling(started_at, now, ceiling)` (rules may NOT import `tumnis.core.limits`: the rules allow-list is stdlib + pydantic + core.types, so the ceiling is a parameter), `DispatchTask(label, status, active_kinds)`, `DispatchProfile(status)`, `can_dispatch(task, kind, profile|None) -> Refusal|None` with codes `label_not_runnable`, `status_not_runnable`, `no_ready_profile`, `run_already_active`. Ready profile = status not in {provisioning, not_provisioned, paused}.
- `agents.api`: `request_run(task_id, kind, *, unattended=False, priority=None, rerun_of=None, ctx=None) -> UUID`; `configure_runs(active_cap_seconds=None, wall_clock_ceiling_seconds=None)` (no args = plan defaults); `signal_run(ctx, run_id, kind)` (emits `run.signal`; "waiting" used by T-18, P2-05 will use it); `accept_result`, `PostResultIn`, `ResultOut` (fields incl. `id`, `run_id`, `task_id`, `summary`); `cancel_run`, run view, events page.
- REST: `POST /v1/tasks/{task_id}/run` -> 202 `{run_id, status: "queued"}`, 409 `run_already_active`; `POST /v1/runs/{run_id}/result` -> 200 (task token; idempotent on run: a second post returns the first result), 403 `run_mismatch`; `GET /v1/runs/{run_id}/events?after_seq=&limit=` -> `{items: [{seq, message_id, kind, payload, ...}], ...}` (not `Page[...]`: declare a dedicated model or `unpaginated_reason`); `GET /v1/runs/{id}`; `POST /v1/runs/{id}/cancel` -> 202.
- DB (new migrations; report ids): `agents_0005` after `agents_0004`: `runs.state_seq`, `active_seconds_used`, `runner_id`, `stop_reason`, `packet jsonb` (token redacted), `rerun_of`, `tasks_created` (workflow_id and profile_version already exist); `run_events.seq` (sequence default + backfill, unique `(workspace_id, run_id, seq)`); partial unique index `ux_runs_ws_task_kind_active ON runs (workspace_id, task_id, kind) WHERE status IN ('queued','running','waiting_on_human','held')` (the double-run guard; map IntegrityError to 409). `tasks_0007` after `tasks_0006`: `results(task_id, run_id, summary, files_touched jsonb, links jsonb, tests_summary, outcome)` via `create_tenant_table`; check `row_factory.COLUMN_VALUES` for the A0.3 sweep.
- Tests read: `runs.stop_reason` (`time_limit`, `wall_clock_ceiling`, `runner_lost`), `runs.rerun_of`, `run_events.seq`, a final `log` run event with payload text "Stopped at the time limit" (T-04) and "Stopped: the runner stopped answering" (T-08), review item kind `run_limit` with target (`run`, run id), review item kind `result` with target (`task`, task id) and payload `{run_id, summary, files_touched, links, ...}`, outbox `run.finished` payload `{run_id, status, ...}` exactly once per run, outbox `result.posted` payload with `run_id`.
- Kill points (T-05..07): `agents.dispatch_run.after_send` (workflow body right after `send_to_agent`), `agents.send_to_agent.after_dispatch` (inside the step after the adapter dispatched), `agents.dispatch_run.waiting_recv` (in `_supervise` just before the first `recv`). Use the existing `tumnis.core.faults.killpoint` / `TUMNIS_KILLPOINT`.

## Decisions settled (with the advisor) — follow them

1. **One result path.** `ws._result` for a run whose `workflow_id == str(run_id)` (a `dispatch_run` run) must NOT `send` to the workflow; it calls `accept_result(via="runner")` (succeeded, output_json parsed leniently into PostResultIn) or emits `run.signal{agent_failed}`; `run_skill` runs (`workflow_id` `run_skill:<id>`) keep the old direct send (P2-02's T-15 runs a `task` packet through `run_skill`). `accept_result` emits `run.signal{result}` in its transaction; subscriber `deliver_run_signal` does `DBOS.send_async(str(run_id), msg, topic="run:<run_id>", idempotency_key=<event id>)`.
2. `_supervise` also normalises legacy messages (`{"status": "runner_lost"}` from the old sweep path, `{"status": "cancelled"}` from `hermes._tell_workflow`).
3. **`finish_run` tolerates an already-terminal row** (the sweep and the protocol-1 cancel fallback write statuses directly): return the stored status and seq, emit nothing twice. It revokes tokens via `api.run_ended` (P2-02, decision 31), emits `run.finished`, writes the closing system log line, adds `run_limit` for time limits.
4. **Actors.** `ACTOR_REF_PATTERN` allows only system|user|api_key|task_token|device; task edges: START (B/T->P) human or agent, P->R agent only, R->D and R->P human only. So: `prepare_run` moves the task to in_progress with the run's requester (`runs.created_by`) when it is a user/agent (skip the move if the task is already in progress; system requesters skip too); runner-side `accept_result` acts as `device:<runner>` (agent); the `post_result` REST twin sets `session_twin_allowed=False`; the `result` decision handler (the ONE agents subscriber `agents.apply_review_decision`, decision 29: extend it, do not add a second) uses `EventEnvelope.actor` (the deciding user; the envelope carries it) for R->D / R->P, `add_comment`, and `request_run(rerun_of=...)`.
5. **No dedup id on the partitioned `runs` queue** (DBOS 3.1.0 refuses it): deterministic workflow id `SetWorkflowID(str(run_id))` + the partial unique index. PR body deviation.
6. **`priority_enabled` does not exist** in DBOS 3.1.0 `register_queue`; every queue is a priority queue (`dbos/_sys_db.py` ~7000 writes `priority_enabled: True` always; Context7 /dbos-inc/dbos-docs queue tutorial: priority via `SetEnqueueOptions(priority=)`, lower = higher). PR body deviation; cite both.
7. **Enqueue from a subscriber** needs `asyncio.get_running_loop().create_task(enqueue(), context=contextvars.Context())` like `workflows.start_provision` (DBOS refuses starting a workflow from inside the delivery step). Same for `DBOS.send_async` if it refuses inside a step.
8. Partition key for dispatch_run = `str(project_id)`; queue `runs` stays `partition_concurrency=2` (consider `polling_interval_sec` 0.5 so T-01's timing holds).
9. Configurable durations: `AgentsSettings.run_active_cap_seconds` / `run_wall_clock_ceiling_seconds` (fakes mode only), `agents.api.configure_runs`, wired in `worker.configure_agents`. Active cap default = project policy `max_run_minutes * 60`.
10. Runner sweep: for dispatch_run runs (running or waiting_on_human on an offline runner) emit `run.signal{runner_lost}` in the sweep step's transaction instead of flipping the row; keep the old direct flip + send for run_skill runs (P1-04 tests).
11. Cancel of a still-queued run: flip queued->cancelled directly and emit run.finished; `prepare_run` then sees a terminal row and the workflow ends.
12. Store the packet (token REDACTED) in `runs.packet` inside `send_to_agent`; T-11 reads the rerun's packet from the fake runner, which must carry the feedback comment (build_packet gathers comments).
13. `stop_agent` with a protocol-1 runner: hermes cancel marks the run `cancelled` itself; finish_run then keeps `cancelled` (known edge, document). Tests use protocol 2.
14. Log lines over 8 KiB cut with a marker; text through `tumnis.core.logging.scrub` before storage (`record_run_events`, used by ws `_run_report`).
15. `result/task_result/1` schema: not required by any spec test; decide and state it in the PR body.

## Scott / coordinator items

- **A2.1 / `frontend/e2e/journeys/J3.spec.ts` does not exist**: no phase-2 acceptance PR (`[P2-00] spec: phase 2 acceptance suite`) has landed, so P2-04 cannot "un-fail A2.1". Don't author the suite in P2-04; report it.
- R-29's 24-hour ceiling remains a plan default flagged for Scott.
- Open item unchanged: who mints a profile's API key (keyless profiles still dispatch without a token).

## Verify commands

```bash
cd backend && uv run pytest -q -p no:randomly tumnis/modules/agents/tests/unit/test_run_rules.py   # sandboxed
cd backend && uv run pytest -q --collect-only -m integration tumnis/modules/agents/tests/integration
make check          # from the worktree root
make test           # bare, unit + contract
make test-int       # bare, integration (VM loaded: CI is the judge)
```

Push with `/usr/bin/git push origin HEAD:wp/P2-04`. Use `/usr/bin/git`, never plain `git`. Never merge.
