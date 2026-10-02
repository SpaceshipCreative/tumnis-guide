# HANDOFF: P4-04 Unattended run windows (continuation c1 -> c2)

PR **#155** (DRAFT): https://github.com/SpaceshipCreative/tumnis-guide/pull/155, branch `wp/P4-04`.
CodeRabbit: not requested yet (request ONCE when marking ready: `gh pr comment 155 --body "@coderabbitai review"`, then `gh pr ready 155`).
Coordinator (binding): CI runs only on PRs and only when the PR is mergeable (a merge conflict = no CI run); decision 78 needs a CI run id before a marker comes off. #152 takes agents_0011; P4-04 needs no agents migration.

## Commits (c1)
- `8e3977d2` feat(tasks): queue flag + routes (tasks_0010 `unattended_queued_at/by`; `queue_unattended`, `get_unattended`, `may_run_unattended_for`, `unattended_queue`, `consume_unattended`; GET/PUT `/v1/tasks/{id}/unattended`, session only; `TaskOut.unattended_queued_at`)
- `57c56dc6` feat(agents): additive: `ResultPayload.batch/release_at`, `register_result_batch(lookup)` used by `accept_result`
- `821d7acf` feat(planning): planning_0004 (`unattended_windows`, `unattended_runs`), window get/put, `unattended_refused` review kind (accept = unqueue via on_decide), `queued_unattended` (day summary + `queued_overnight`), `run_unattended_tick`, `unattended_tick_for`, `unattended_workspaces`, result-batch hook, `unattended_runs_per_week` metric (dropped from `_LATER`); router `GET/PUT /v1/unattended/window`; workflows `unattended_tick` + schedule `unattended-tick` */5 on maintenance queue
- `c57d6cee` feat(notifications): notifications_0003 (decision `overnight` NOT VALID check, `release_at`, partial index); `record_review_item` holds items whose payload.batch == "overnight"; `release_overnight(ctx, now)` + `overnight_workspaces()`; rules `overnight_due`, `overnight_payload`; workflow `notifications.release_overnight` + schedule `overnight-release` */5; `notifications/testing.py` tick (enqueues push via DBOSClient `workflows.push_options`)
- `24191dec` chore(api): make gen
- `7336850f` test(planning): markers off for T-P4-04-01..04 (37 cases XPASS in CI run 36959204086)
- `aa598443` test(notifications): overnight rules unit tests
- `2af0476a` Merge origin/main 67a43da (#138, #153): planning/testing.py now holds BOTH `planner-tick` (from #138) and `unattended-tick`; router imports `testing as _testing` once

## CI state: run 36960927427 on 2af0476a
unit, lint, contract, integration-a, integration-b FAILED; others green (e2e, performance, security, spec-guard, traceability, red-proof).
Known causes to fix first:
1. **lint**: `frontend/src/components/task/RunUnattendedToggle.tsx` stub (`_props` unused). Fixed by the real component (frontend work below).
2. **Isolation suite** (integration-b, maybe contract): `ERROR at setup` for every `test_isolation[...]`, `test_counters_are_per_workspace`, `test_every_tenant_table_has_rows_in_both_workspaces`: almost certainly the new tenant tables `unattended_windows` and `unattended_runs` need rows in the two-workspace isolation seed/catalog (`backend/tests/isolation/` and/or `backend/tests/meta/_catalog.py`; see how planning_0003's `plan_issues`/`plan_pins` are seeded there). Read `gh run view 36960927427 --log-failed` for the exact setup error.
3. Read unit/contract/integration-a failures in the same log (`gh run view 36960927427 --log-failed | grep -E "FAILED|XPASS|Error"`). Expected XPASS(strict) on P4-04 integration tests that now pass (T-05..11, A4.3): remove each marker citing the run id (decision 78), only after CI shows XPASS. Contract job may fail on a golden/schema for `TaskOut` (tools.json changed) or the new review kind.

## Frontend: NOT DONE, NOT PUSHED
A frontend subagent ("P4-04 frontend UI", agent aabb8f747ff29349f) was still running at handoff in c1's worktree `/home/claude/Projects/Tumnis-Guide/.claude/worktrees/agent-a9e2485b083955a01`. Its work is UNCOMMITTED there (not in this handoff commit): modified CloseDayPanel.tsx, TaskDrawer.tsx, ScheduleSection.tsx, review/ResultItem.tsx, ReviewItemCard.tsx, review/slots.tsx, settings/queries.ts, settings/sections.ts, RunUnattendedToggle.tsx, routes/settings.$section.tsx, test/msw/closeDay.ts, test/msw/handlers.ts; new settings/UnattendedSection.tsx, UnattendedWindowForm.tsx, test/msw/unattended.ts and tests CloseDayPanel.unattended, ScheduleSection.unattended, review/UnattendedRefused, settings/UnattendedSection, task/RunUnattendedToggle.queue (.test.tsx). If c2 runs in that worktree: wait for the subagent (or read its output), review, typecheck/lint/Vitest, then commit. If c2 runs elsewhere: copy those files over, or redo. Unverified; not reviewed by c1. Scope:
- `frontend/src/components/task/RunUnattendedToggle.tsx`: AI untainted -> switch role=switch name "Run unattended" (checked when `task.unattended_queued_at`), PUT `/v1/tasks/{id}/unattended {queued}`, invalidate task + day summary; tainted AI -> text "Needs you: from outside content"; Human/Hybrid/null -> render nothing. Spec test T-P4-04-12 `RunUnattendedToggle.test.tsx` (test.fails; remove after CI shows "expected to fail but passed"). Place it in the TaskDrawer (no new GET on drawer open).
- Settings: workspace "Unattended runs" section beside WorkingHoursSection (+ `settings/sections.ts`); per-project override in `project/rail/ScheduleSection.tsx` (`window: null` clears; shows source).
- CloseDayPanel "Queued overnight": `queued_unattended` with "Will not run" + reason; keep "Nothing queued" empty state; add `queued_unattended: []` to `frontend/src/test/msw/closeDay.ts` fixtures.
- Review page: `unattended_refused` items (reason, Accept/Snooze), overnight results grouped "Overnight".
- MSW handlers for every new request (decision 22); DS-01 primitives, no Mosaic copy, 375 px; Vitest tests `[P4-04][FR-4.5]`.

## Remaining steps
1. Fix CI (above), push, re-read CI; remove P4-04 markers per test once CI shows XPASS (cite run id).
2. Frontend (above), `npm --prefix frontend run typecheck/lint`, Vitest.
3. `make check` (semgrep env vars under $TMPDIR/P4-04-c2/), push, full PR body (template below), `gh pr ready 155`, request CodeRabbit once, review loop, then SendMessage main "#155 MERGE-READY at <sha>".
4. Delete HANDOFF.md in a chore commit when done.

## PR body must include
- Deviations: A4.3 written by P4-04 from the plan text (P4-00 suite never committed); unattended runs recorded in planning's own `unattended_runs` table instead of a `runs.unattended` column (no agents migration); additive agents edit (ResultPayload fields + result-batch hook); overnight hold is a new notifications decision value `overnight` with a separate `overnight-release` schedule (not the natural-break flush); schedules registered through each module's `workflows.schedules()` (no worker.py edit); `batch_release_at` clamps to the window end; request_run refusals (409/422) become `unattended_refused` items with the problem code as `refusal`; window PUT version is the stored row's version (null when none); no kill-and-resume test for `unattended_tick` / `release_overnight` (replay safety: flag consumed in the request transaction, dedupe keys; T-07 proves one request per task across ticks), no meta sweep requires it; `unattended_runs_per_week` now computed (removed from "phase 4" later list).
- Docs cited: DBOS Python `DBOSClient.enqueue_async(options, *args)` / `EnqueueOptions` (Context7 /dbos-inc/dbos-docs, docs/python/reference/client.md); `DBOS.apply_schedules` schedule dicts (Context7 /dbos-inc/dbos-docs 3.1, per c0); hypothesis (c0, T-04).
- Scott items: plan DONE item "a real overnight run on the homelab lands in the morning review" is Scott's.

## Deviations / Scott items carried from c0
- A4.3 added by P4-04 (P4-00 suite missing). Planning-owned `unattended_runs` table. Additive agents edit. Overnight hold as a new decision value. `batch_release_at` clamps to window end. Homelab overnight run is Scott's.

## Verify
- `cd backend && uv run alembic heads` (tasks_0010, planning_0004, notifications_0003; agents stays agents_0010)
- `cd backend && uv run pytest -q tumnis/modules/planning/tests/unit tumnis/modules/notifications/tests/unit`
- `gh run view 36960927427 --log-failed`
