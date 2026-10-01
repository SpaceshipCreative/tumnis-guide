# P1-11 handoff (Daily plan), continuation c1 -> c2

Branch: `wp/P1-11` (push with `/usr/bin/git push origin HEAD:wp/P1-11`), based on main a0b4515.
**No PR opened yet** (CI runs only on PRs). No CodeRabbit threads. No CI yet.

Original task prompt: `/tmp/claude-1002/coord7/p1-11.txt` (plus the coordinator rule files it names).

## Commits on wp/P1-11 (after main a0b4515)

- bcf88ba chore: P1-11 handoff (c0)
- c48ac65 test(planning): P1-11 spec tests (red) — T-P1-11-01..19 strict xfail + stubs; calendar.api re-exports
  CalendarSyncedV1/SyncWindow (T-15 imported calendar.payloads, a boundary break; import changed to calendar.api,
  no assertion touched); `_plan.py` helper typing fixes; two unused `type: ignore` removed in test_validate_plan.py.
- 3c4264d feat(planning): plan rules — T-01..07 green, markers removed.
- 411f008 feat(agents): planning request assembler — T-19 green, marker removed.
- 0f56950 feat(planning): build_plan, planner_tick, plan actions and routes (integration markers still on).
- (this commit) test(planning): integration conftest + build_runs helper fix, and this handoff.

## Integration status (focused local runs, `--runxfail`, see "Focused local runs" below)

PASS (actually ran green; remove their `xfail(strict=True, reason="spec:P1-11")` markers):
- T-09 test_master_plan_published_with_blocks_and_reasons
- T-10 test_master_unreachable_uses_fallback_with_notice
- T-11 test_invalid_master_reply_rejected_and_logged (all 4 params)
- T-12 test_unplaceable_pick_gets_issue_and_offer
- T-14 test_replan_on_demand_supersedes_and_respects_now (2nd run, after the build_runs fix)
- T-15 test_nothing_replans_during_the_day (2nd run)
- T-16 test_killed_worker_publishes_once (2nd run, after the integration conftest)
- T-08 test_tick_enqueues_once_per_workspace (2nd run)
- T-17 test_accept_swap_remove
- T-18 test_split_creates_subtasks_and_move_pins
- A1.4 step 1 `tests/acceptance/test_a1_4_degraded_modes.py::test_master_offline_falls_back_to_due_date_order`
  (marker `spec:P1-11`) passed with --runxfail too: remove its marker as well (only that test's marker).

FAIL: T-13 test_blocked_today_task_stays_until_replan:
`assert [p["status"] for p in plans(db)] == ["superseded", "published"]` got `['published','superseded']`.
`plans()` orders by `built_at`; the morning build uses now=PLAN_TIME 12:30Z but the replan route passes the app's
FixedClock now (12:00Z), so the replan's built_at is earlier. Fix in `planning.api.publish_plan`: write
`built_at = max(draft.built_at, built_at of the plan being superseded)` (or + 1 µs when equal), and note it.
Don't change the test.

## Next steps

1. Fix T-13 (above). Remove the passing markers (list above) one by one; re-run focused (below) once to confirm.
2. `make check` (use `bash $TMPDIR/P1-11-c2/check.sh`-style helper: semgrep needs SEMGREP_SETTINGS_FILE,
   SEMGREP_LOG_FILE, SEMGREP_VERSION_CACHE_PATH under $TMPDIR; ~/.semgrep is read-only).
3. 100% line coverage on planning/rules.py (done checklist): run
   `cd backend && uv run pytest -q -p no:randomly --cov=tumnis.modules.planning.rules --cov-report=term-missing tumnis/modules/planning/tests/unit`
   and add unit tests (new test file, not the locked ones) for uncovered lines (free_left, check_picks branches,
   fallback_reason "Overdue since"/"Rolled over once", no-estimate unplaceable).
4. Open PR1: `gh pr create --base main --head wp/P1-11 --title "[P1-11] impl: Daily plan" --body-file <file>`, then
   `gh pr comment <url> --body "@coderabbitai review"`. Then the review loop (pr-review-loop.md).
5. PR2 `wp/P1-11-impl-2` (frontend T-20..23: red commit first, then TodayPanel/PlanItem/FitOfferRow/SwapPicker with
   DS-01 tokens; `frontend/src/lib/live-map.ts` already has the `plan` entity and planningGetPlan/Alternates).

## Focused local runs (how c1 ran single integration tests without loading Docker)

Integration tests only run bare (`make test-int`). c1 put an UNCOMMITTED conftest at
`backend/tumnis/modules/planning/tests/conftest.py` (copy saved at `/tmp/claude-1002/P1-11-c1/focus_conftest.py`)
that, when `/tmp/claude-1002/P1-11-c1/focus.txt` exists, sets `config.option.runxfail = True` and deselects every
item whose node id contains none of the focus file's lines. Then bare `make test-int` runs only those (≈30 s).
Delete that conftest before `make check` (ruff S108) and never commit it.

## Design as built (record deviations in the PR body)

- Migration `planning_0003` (down planning_0002): plan_issues (kind no_gap/no_estimate, offer jsonb,
  review_item_id, resolved_at; unique ws+plan+task) and plan_pins (unique ws+task+day). Deviation: prompt said
  "start planning_0001"; the chain already existed (P1-10/P1-12).
- rules.py: as the plan, plus PRIORITY_RANK, ELIGIBLE_STATUSES, fallback_key, fallback_reason, free_left;
  PlanContext.ahead (dict day -> free blocks) added for move offers. fit_offer rule documented in its docstring.
- workflows.py: planner_tick (`planner-tick`, `*/5 * * * *`, maintenance queue, via `schedules()` picked up by
  worker.register_module_schedules) enqueues build_plan with workflow id `build_plan:<ws>:<day>:morning`
  (deterministic id, not SetEnqueueOptions dedup id: deviation per coordinator note; DBOS 3.1.0 checked: a reused
  id with different inputs is a no-op, `_sys_db.py` only compares function/class/config/queue names).
  build_plan(workspace_id, day, trigger, now): `now` added to the signature (deviation). Master judged by
  `agents.api.master_agent` with the transport's own criteria (runner.status online + profile in inventory), not
  heartbeat age on a clock (FixedClock vs heartbeat would mark it offline). Plan run = child run_skill through the
  new seam `agents.api.register_skill_runner`/`run_plan` (agents.workflows.run_child_skill registers it).
  Reply check: loose parse of picks (schema-invalid replies still name codes), check_picks codes comma-joined in
  fallback_reason, `no_json`, `invalid_output`. Unreachable (timed_out/runner_lost/cancelled/agent_unavailable) ->
  agent_offline. publish_step: plan id uuid5(NS, workflow id); existing id -> no-op; killpoint after commit.
- api.py: PlanningSettings section `planning` (plan_time, plan_weekdays, run_timeout_s 90), plan_issue review
  kind (accept=split, edit=move, reject=keep off, snooze), gather_plan, due_plan_day (morning plan in any status
  counts as built), publish_plan, get_plan, accept/accept_all/remove/swap/alternates, resolve_issue
  (split/move; decides the review item), apply_issue_decision (human.decided subscriber
  `planning.apply_plan_issue`, idempotent), request_replan (DBOS client enqueue, random-suffix id).
  Candidates exclude parents with open subtasks; pins first.
- payloads.py: `plan.published` v1 {plan_id, day, task_ids, reasons, source, trigger}; schemas/events regenerated.
- Shared edits: tasks.api (`HumanDecidedV1` in __all__, `open_tasks`, `tasks_by_ids`), projects.api
  (`active_project_ids`), calendar.api (re-export of CalendarSyncedV1/SyncWindow with noqa PLC0414),
  agents.api (master_agent, planning_request, plan_packet, SkillRunner/register_skill_runner/run_plan,
  MasterAgentOut, __all__), agents.workflows (run_child_skill + registration), agents.packet_builder
  (PlanningProjectIn, assemble_planning_request), tests/fakes/fake_runner.py (title:<t> resolution for plan
  replies, factory `offline(profile)`), frontend/src/lib/live-map.ts (`plan` entity), generated
  schemas/openapi.json + frontend/src/api/*.
- Third-party docs used: Context7 `/dbos-inc/dbos-docs` (workflow IDs and idempotency: reusing an id returns the
  existing workflow; SetEnqueueOptions dedup) + installed dbos 3.1.0 source (`_sys_db.py` conflict check).
  Cite in the PR body.

## Scott items so far

- A1.2/A1.3 (Playwright) likely stay red: the e2e stack has no scriptable fake runner or tick route
  (same as P1-08's For Scott 2).
- Done-checklist items not provable here: kill test 20 runs in a row; the real master on the homelab.
