# HANDOFF: P2-09 (Kill switch, pause and runaway limits)

Branch: `wp/P2-09` (pushed with `/usr/bin/git push origin HEAD:wp/P2-09`). No PR opened yet.
Scratch: `$TMPDIR/P2-09-c0/` (edit scripts, check log). Base: main at fee9afa (#111 merged).

## Commits

- `a59c9e0` test(agents): P2-09 spec tests (red): T-P2-09-01..11, all strict xfail /
  `test.fails`; `_runs.py` helpers (`master_key`, `call_tool`, `audit_rows`,
  `open_pauses`, `step_names`; `_cancel_open_dispatches` also cancels `held` runs).
- next commit (WIP, see below) `feat(agents): P2-09 kill switch backend (wip)`: the whole
  backend is written; `make check` fails only on the Vitest live-map test (below).

## What is built (backend, uncommitted -> committed as WIP)

- rules.py: `PauseView`, `pause_state`, `MAX_TASKS_PER_RUN_DEFAULT`, `over_task_limit`.
  T-P2-09-10 green, marker removed.
- models.py `AgentPause`; migration `agents_0006` (`agent_pauses`, after `agents_0005`;
  partial unique `ux_agent_pauses_ws_open` with NULLS NOT DISTINCT).
- payloads.py: `agents.paused`, `agents.resumed` (+ contract fixtures; `make gen` run).
- api.py: `PauseIn`, `PauseOut`, `ResumeIn`, `ResumeOut`, `OpenPause`, `PausesOut`,
  `pause_state_for`, `pause` (row, queued->held in the same tx, audit `killswitch.on` /
  `project.paused`, emit; an already paused scope answers the open pause), `resume`
  (audit `killswitch.off` / `project.resumed`), `open_pauses`, `runs_to_cancel` (only
  dispatch_run runs: `workflow_id = id::text`), `held_runs_free`, `count_run_task`
  (registered with `tasks.register_run_task_counter`), `request_run` 409 `agents_paused`,
  `finish_run_in` adds `run_limit` item for cancelled + `tasks_per_run`, `cancel_run`
  wakes a held run with run.signal{cancel}; stop reasons `killswitch`, `project_paused`,
  `tasks_per_run` + closing lines.
- workflows.py: `check_pause` step, `_hold_if_paused`, `_wait_while_held` loop in
  `dispatch_run`, `prepare_run` re-checks the pause under its row lock (`Prepared.held`),
  `send_to_agent` checks the pause first (`RunHandleData.paused` -> cancelled), kill point
  `agents.dispatch_run.after_prepare`, `release_held`.
- events.py: `agents.cancel_paused_runs` (emits run.signal cancel per run, one tx) and
  `agents.release_held_runs` (sends `release` with key `<event>:<run>`).
- mcp.py: `pause_agents` op (scope `delegate`, `master_only=True`, project_arg
  `project_id`, REST twin `POST /v1/agents/pause`); a project-limited caller cannot pause
  the workspace (404). `PENDING_TOOLS` no longer lists it.
- router.py: `GET /v1/agents/pause` (session; plan addition for the UI),
  `POST /v1/agents/pause` (session -> api.pause directly; key -> rest_twin, so non-master
  keys get 403 master_only), `POST /v1/agents/resume`, `POST /v1/projects/{id}/pause`,
  `POST /v1/projects/{id}/resume` (all session only). All answer 200 (parity test needs
  200/201 on the twin).
- tasks: `register_run_task_counter`, `create_task(run_id=...)`, tasks/mcp.py passes
  `call.caller.run_id`.
- Helpers: `tests/_mcp.py` (SAMPLES `pause_agents` with scope project; the ALL_SCOPES
  unrestricted key is always the master, so the P2-01 sweeps' writer reaches the
  master-only op), `tests/audit_cases.py` (4 cases).

## Remaining steps (exact)

1. Fix `make check`: `frontend/src/lib/live-map.test.ts` fails with
   `expected ['agentsGetPauses'] to deeply equal []` -> the new GET query must be mapped
   in `frontend/src/lib/live-map.ts` (see how P2-04 added the `run` entity) or listed as
   not live there. Then `make check` green (run with SEMGREP_SETTINGS_FILE,
   SEMGREP_LOG_FILE, SEMGREP_VERSION_CACHE_PATH under `$TMPDIR/P2-09-c0/`).
2. Frontend: `frontend/src/components/dashboard/KillSwitch.tsx` (DS-01 `ui.ts`
   primitives; pause button "Pause all agents" -> dialog "Pause all agents" with textbox
   "Reason" and button "Pause" disabled until a non-blank reason; POST body
   `{scope: "workspace", reason}` with Idempotency-Key (`apiWrite`/`useWrite` from
   `lib/fetch`); banner `role="status"` named "Agents paused" showing the reason and a
   "Resume agents" button posting `/v1/agents/resume` `{scope: "workspace"}`); mount it in
   `DashboardPage.tsx` header; add a default MSW handler for `GET /v1/agents/pause`
   (`{schema_version:1, workspace:null, projects:[]}`) in `frontend/src/test/msw/handlers.ts`
   so dashboard tests stay green; flip T-P2-09-11 `test.fails` -> `test`. Project pause:
   `frontend/src/components/project/AgentRail.tsx` does not exist (P2-17 lands the rail);
   build the smallest seam (e.g. a project pause control in the project's right rail) and
   note it. Check at 375 px.
3. Push; CI integration shows which spec tests XPASS(strict): remove those markers
   (T-P2-09-01..09 and A2.5 `backend/tests/acceptance/test_a2_5_kill_switch.py`) only
   after they pass. Also watch the P2-01 meta sweeps (test_mcp_*), T-P0-15-07 audit cases,
   table registry, route registry, authz matrix.
4. Open PR `[P2-09] impl: Kill switch, pause and runaway limits`, comment
   `@coderabbitai review`, run the review loop, send `#<PR> MERGE-READY at <sha>` to main.

## Deviations (for the PR body)

- `PauseIn` is a plain model in api (the MCP input `PauseAgentsIn` adds the
  idempotency key), not a `WriteInput`.
- `POST /v1/agents/pause` serves a session directly (the op is master-only); keys go
  through the op. Resume takes `{scope, project_id?, reason?}`.
- `GET /v1/agents/pause` is a plan addition (the UI needs the state).
- No pause-state cache: one indexed query per check; no staleness.
- The kill switch cancels and holds `dispatch_run` runs only; `run_skill` runs
  (enrichment, planning: P1-04/P1-08/P1-11) end on their own timeouts (Scott item).
- `release` is sent directly by the resumed subscriber (no new `run.signal` kind).
- Project pause stop reason is `project_paused` (workspace: `killswitch`).
- The `run_limit` review item comes from `finish_run_in` (cancelled at a limit), not
  from the separate transaction, which only emits `run.signal{limit}`.
- DBOS 3.1.0: no dedup id on the partitioned runs queue (P2-04's deterministic workflow
  ids kept).

## Docs relied on

- Context7 `/dbos-inc/dbos-docs`: `DBOS.send` idempotency key (scoped per destination),
  `recv` timeout and topics; messages persist so a send to an enqueued workflow is
  received later.

## Verify

- `cd backend && uv run pytest -q -m "not integration and not contract" tumnis/modules/agents/tests/unit/test_pause_rules.py`
- `make check`; integration via CI (Docker load rule).
