# P1-11 handoff (Daily plan)

Branch: `wp/P1-11` (pushed with `/usr/bin/git push origin HEAD:wp/P1-11`), based on main a0b4515.
No PR opened yet. No CodeRabbit threads. No CI yet.

Task prompt: `/tmp/claude-1002/coord7/p1-11.txt` (read it first, plus the coordinator rule files it names).

## State

Done (uncommitted work committed in the handoff commit):
- Red spec tests written, NOT yet green-committed as a proper red commit, because `make check` fails on mypy
  (see "First step" below):
  - `backend/tumnis/modules/planning/tests/unit/test_validate_plan.py` T-01..04 (table + 3 Hypothesis properties, 200 examples)
  - `backend/tumnis/modules/planning/tests/unit/test_fit_offer.py` T-05
  - `backend/tumnis/modules/planning/tests/unit/test_is_plan_due.py` T-06, T-07
  - `backend/tumnis/modules/agents/tests/contract/test_planning_request.py` T-19
  - `backend/tumnis/modules/planning/tests/integration/{_plan.py,test_planner_tick.py,test_build_plan.py,test_plan_actions.py}` T-08..18
  - recordings `backend/tests/fakes/recordings/runner/plan__{monday_four_picks,ninety_first,six_picks,unknown_task,no_reason,no_json}.result.json`
    (picks name tasks as `"title:<task title>"`; the fake runner must resolve them, see below)
- Unit + contract run: 33 passed, 44 xfailed (all spec tests red). Integration collects.

## First step for the next agent

`make check` fails: mypy `attr-defined` on names that do not exist yet (rules.PlannedItem, validate_plan, PlanContext,
PlanPick, assign_blocks, fallback_plan, fit_offer, is_plan_due, MAX_ITEMS, MIN_SPLIT_CHUNK, DEFAULT_PLAN_TIME,
DEFAULT_PLAN_WEEKDAYS, workflows.build_plan/planner_tick, api.plan_candidates, packet_builder.PlanningProjectIn /
assemble_planning_request) plus three small ones in `_plan.py` (unused type: ignore at line ~103; `change_status`
wants `Status` -> pass `tasks.Status(to)`; `until(builds_settled)` typing -> make `builds_settled` sync or loosen `until`'s
type to `Callable[[], Any]`). Fix: either add minimal stubs that raise NotImplementedError (cleanest for a red commit),
or start implementing right away and do the red commit once names exist. Then commit `test(planning): P1-11 spec tests (red)`.
Never change an assertion afterwards.

## Design decided (follow it; record deviations in the PR body)

- Migration `planning_0003` (down `planning_0002`): `plan_issues(plan_id, task_id, kind, offer jsonb, review_item_id, resolved_at)`
  and `plan_pins(task_id, day, UNIQUE(workspace_id, task_id, day))` via `create_tenant_table`. daily_plans/plan_items already
  exist from P1-12 (planning_0002). Deviation: prompt said "start planning_0001"; the chain exists.
- rules.py (pure): MAX_ITEMS=5, MAX_REASON=140, SLOT_ROUNDING_MIN=5, MIN_SPLIT_CHUNK=15, MOVE_LOOKAHEAD_WORKING_DAYS=5,
  DEFAULT_PLAN_TIME=time(8,30), DEFAULT_PLAN_WEEKDAYS=frozenset(0..4); PlannedItem(task_id, position, reason, block: Interval|None);
  PlanPick(task_id, reason); PlanContext(day, tz, now, free_blocks, tasks, replan, ahead: dict[date, list[Interval]] = {});
  Unplaceable(task_id, reason, offer); FitOffer(split, move_to). PlanTask.priority int: urgent 3, high 2, normal 1, low 0.
  validate_plan check order as plan; tests compare SETS of codes. eligible = status in {backlog,today,in_progress} and
  project active; blocked = waiting_on_human. Keep validate_plan free of helpers assign_blocks uses.
- assign_blocks: in pick order; AI -> no block; Human/Hybrid without estimate -> Unplaceable(empty offer); else earliest
  remaining segment where start = round_up(max(seg.start, floor), 5 min) and start+est <= seg.end; floor =
  round_up(now,5) only on replan; segment shrinks. Unfit -> Unplaceable with fit_offer(task, remaining segments, ctx.ahead).
- fallback_plan: eligible unblocked tasks sorted (due_on nulls last, -priority, -rollover_count, created_at, task_id);
  place each with the same placement; stop at 5 placed; skip unfit (report Unplaceable only for tasks due on/before day);
  reasons "Due Tue 10 Mar" / "Rolled over 3 times" / "Oldest open task". Matches A1.4's `_fallback_key`.
- fit_offer rule (docstring it): split = fill today's blocks largest first (chunk = min(block, left)); the leftover becomes one
  last chunk; a last chunk under 15 is raised to 15 by taking from the previous chunk (which must stay >= 15); offer only
  if >= 2 chunks, every chunk >= 15 and the leftover <= the largest chunk; else None. move_to = first day in `ahead`
  (sorted) with a block >= estimate. Table: 90/[60,45]->[60,30]; 90/[60]->[60,30]; 90/[20]->None; 20/[15]->None; 90/[80]->[75,15].
- is_plan_due(now, tz, plan_time, weekdays, already_built): local day of now; weekday in set; not built; now >= local_to_utc(day, plan_time, tz) (fold=0).
- check_picks(picks, ctx): too_many_items, duplicate_task, unknown_task, ineligible_task, missing_reason, reason_too_long.
  Reply check is lenient: parse `picks` from raw JSON loosely so a 6-pick or empty-reason reply (schema-invalid) still
  names its codes; no output -> "no_json". fallback_reason = comma-joined codes. Master unreachable (not_provisioned,
  offline, paused, run timed_out/runner_lost/agent_unavailable) -> notice agent_offline; bad reply -> invalid_plan.
- Workflows (`planning/workflows.py`): `@DBOS.workflow(name="planner_tick")(scheduled_time, context)`,
  `@DBOS.workflow(name="build_plan")(workspace_id: UUID, day: date, trigger: str, now: datetime) -> UUID` (now is an added
  argument: scheduled_time for morning, request clock for replan). planner_tick enqueues on `maintenance` with
  `SetWorkflowID(f"build_plan:{ws}:{day}:morning")` (deterministic id per coordinator; note maintenance is not partitioned,
  so the plan's dedup id would also have worked). Replan: random suffix id, enqueued from the api through
  `deadletter.dbos_client().enqueue_async({"queue_name": "maintenance", "workflow_name": "build_plan", "workflow_id": ...})`.
  Schedule via `schedules()` in planning/workflows.py (`planner-tick`, `*/5 * * * *`, queue maintenance); worker's
  `register_module_schedules` picks it up.
- publish_step: plan id = uuid5(NS, DBOS.workflow_id); INSERT ... ON CONFLICT DO NOTHING; supersede previous published
  plan of the day; items, issues (+ `plan_issue` review item via tasks.api.add_review_item, kind registered at planning.api
  import with actions accept/edit/reject/snooze, impact_scope "task"), `plan.published {day, task_ids, reasons, source}`
  in the same transaction; `faults.killpoint("planning.publish_step")` after the commit. Store master_run_id, profile_version.
- run_skill from planning: add a seam in agents.api like `register_provision_starter` (agents.workflows registers
  `run_skill` at import); build_plan: run_id = uuid5(ns, DBOS.workflow_id); `with SetWorkflowID(run_workflow_id(run_id))`
  await the seam; packet kind PLAN, skill "plan", output_schema planning/result v1, timeout 90 s (configurable), prompt
  via render_prompt. Master availability: new agents.api function mirroring agent_for_project for role="master"
  (returns availability, profile_id, profile_version); judge heartbeats on the build's `now`.
- agents.packet_builder: `PlanningProjectIn(id, name, health, next_milestone, brief)` and pure
  `assemble_planning_request(day, timezone, now, window, free_blocks, events: [(title,start,end)], candidates: [TaskOut],
  projects, agents)` (cap 60, brief cut to 600, age_days from now); async gatherer in agents.api reads project context,
  `knowledge.get_brief` ("" on NotFound) and `master_registry`. Do NOT add a "today" flag to PlanCandidate (frozen P1-05
  schema; note deviation).
- Candidates: plan_pins of the day first, then fallback order, Today tasks included; `planning.api.plan_candidates(ctx, day) -> list[UUID]`.
- Free blocks: morning uses day_calendar; replan/weekend computes with working_window(replan=True) outside the cache.
  `ahead` = next 5 working days' free blocks.
- Routes (all `auth="session"`, writes `idempotent=True`): GET /v1/plan/{day} (404 when none; id, day, status, source,
  trigger, notice, items with removed/swapped ones too + live `blocked`, issues), POST /v1/plan/replan (202, body {day?}),
  POST /{day}/items/{task_id}/accept|remove|swap ({with_task_id}), POST /{day}/accept-all, GET /{day}/alternates
  (bare list -> `unpaginated_reason`; fallback-ordered eligible tasks not in the live plan), POST /{day}/issues/{id}/split|move.
  Accept: change_status(to="today") only from backlog; set accepted_at. Accept/remove/swap emit tasks.api HumanDecidedV1
  (add "HumanDecidedV1" to tasks.api `__all__`; list as shared edit). Split/move do the effect synchronously
  (subtasks Human with offered estimates + parent's first_action; or plan_pins row for move_to) and decide the review
  item via tasks.api.decide_review_item in the same transaction; a `human.decided` subscriber
  (`planning.apply_plan_issue`) applies the same effect idempotently (check resolved_at) for review-queue decisions.
- Fake runner (shared test file `backend/tests/fakes/fake_runner.py`): resolve `"title:<t>"` in `picks[].task_id` and
  `alternates[]` against the packet's `body.candidates` titles (unknown -> fixed unknown UUID). Optionally add a
  factory-level `offline(profile)` for A1.4 step 1 (it calls `fake_runner.offline(MASTER)` on the factory).
- `make gen` after routes; commit schemas/ and frontend/src/api/.
- PR split: PR1 `wp/P1-11` = backend (T-01..19) titled "[P1-11] impl: Daily plan"; PR2 `wp/P1-11-impl-2` = frontend
  (red commit of T-20..23 first, then TodayPanel/PlanItem/FitOfferRow/SwapPicker), and A1.2/A1.3 Playwright if the
  e2e stack can run a scriptable fake runner + `POST /v1/test/tick/{schedule}` (likely not: A1.1 stayed red in P1-08 for
  the same reason; keep markers and flag for Scott).

## Verify commands

- `cd backend && uv run pytest -q -m "not integration and not contract" tumnis/modules/planning/tests/unit`
- `cd backend && uv run pytest -q tumnis/modules/agents/tests/contract/test_planning_request.py`
- `make check` (root), `make test-int` (bare, at most once per continuation; CI is the authority)

## Scott items so far

- A1.2/A1.3 (Playwright) and A1.4 step 1 likely stay red: seed lacks `Acme site`/`tumnis-master`, and the e2e stack has no
  scriptable fake runner or tick route (same as P1-08's For Scott 2).
- Done-checklist items not provable here: kill test 20 runs in a row; the real master on the homelab.
