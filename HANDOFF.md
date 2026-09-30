# HANDOFF: P1-13 Review queue v1

Stopped on the coordinator's "HANDOFF NOW" (context watcher). No PR is open yet.

## State

- Branch `wp/P1-13` (local worktree branch renamed; config write failed but the rename held). Pushed to `origin/wp/P1-13`.
- Commits on top of main 989c6c6:
  - `8b91a89` test(tasks): P1-13 spec tests (red)
  - (this commit) chore: P1-13 handoff (HANDOFF.md, HANDOFF-rules.patch)
- Red confirmed locally: 12 pytest unit cases xfail (T-01..04, T-11); 6 integration tests collect (T-05..10, not run: Docker-only); Vitest T-12..14 = 3 expected fails.
- `make check` on the red commit: backend (ruff, format, mypy, lint-imports, unit) passed; frontend typecheck and lint pass after fixes. Vitest had load-driven timeouts (load avg ~46). `src/components/project/ProjectList.test.tsx` fails locally under load even alone. I did not touch that file. Check it in CI.
- `HANDOFF-rules.patch`: the tasks `rules.py` additions, written and not yet applied: GraphTask, TaskGraph, downstream, deterministic_impact, jev_factor, ReviewRow, review_key, review_order, primary_action, plus the constants. Apply it with `/usr/bin/git apply HANDOFF-rules.patch` as the first green step, remove the T-01..04 markers one at a time, and delete the patch file before the PR.

## Spec tests (all red, strict markers)

| ID | File |
| --- | --- |
| T-P1-13-01..03 | backend/tumnis/modules/tasks/tests/unit/test_review_order.py |
| T-P1-13-04 | backend/tumnis/modules/tasks/tests/unit/test_downstream.py |
| T-P1-13-11 | backend/tumnis/modules/tasks/tests/unit/test_review_registry.py |
| T-P1-13-05..10 | backend/tumnis/modules/tasks/tests/integration/test_review_queue.py |
| T-P1-13-12..14 | frontend/src/components/review/ReviewQueue.test.tsx (MSW: frontend/src/test/msw/review.ts) |

## Interfaces the tests fix (implement to these)

tasks (`review.py`, re-exported by `tasks/api.py`):
- `ReviewKindSpec` gains `action_payloads: Mapping[str, type[BaseModel]]` (default empty; backward compatible).
- `validate_payload(kind, payload) -> dict`: the add path, which raises pydantic ValidationError.
- `validate_decision(spec, action, payload, *, snooze_until, now) -> dict | None`: 422 `action_not_allowed`, 422 `invalid_review_payload`, and 422 for a snooze without a future `snooze_until`.
- `ReviewItemOut` with `from_row(row)`. Fields: id, kind, project_id, target_type, target_id, target_title, payload, blocking_impact (float), jev_factor (float, 1.0 when null), actions, primary_action, snoozed_until, decided_at, decision, version, created_at, updated_at.
- `list_review_items(s, *, now, kind=None, cursor=None, limit=50) -> Page[ReviewItemOut]`: open and unsnoozed. Keyset order `-(blocking_impact::float8 * coalesce(jev_factor,1))`, created_at, id. This matches `rules.review_key`.
- `get_review_item(s, item_id)`.
- `decide_review_item(item_id, *, action, payload, snooze_until, version, actor, now=None, session=None) -> ReviewItemOut`: versioned (409 `stale_version`). Snooze sets `snoozed_until`; any other action sets decided_at and decision. Emits `human.decided` (R-07). Its `decision_id` is the item payload's `decision_id`, not the blocking-impact one. Also calls `mark_changed("review_item")`.
- `add_review_item` computes `blocking_impact` at insert (the downstream of the kind's scope) and emits a new event `review_item.added` {item_id, kind, project_id, target_type, target_id}. The event needs its fixture `backend/tests/contract/fixtures/events/review_item.added/v1.json`, then `make gen`, then an A8 row in Part A of the plan.
- Subscribers (names are fixed by the tests):
  - `tasks.refresh_review_impact` on task.created, task.status_changed and task.updated. It recomputes blocking_impact for the open items of the task's project.
  - `tasks.apply_review_decision` on human.decided. For `low_confidence_label`: accept sets label = suggested (source user), edit sets payload.label, reject does nothing. For `estimate_outlier`: edit sets estimate_minutes.
  - `agents.apply_review_decision` on human.decided. For `provisioning_failed`, accept calls `agents.api.retry_provision(project_id)`. Seam: this sets the project's agent_profiles row from not_provisioned to `provisioning`. P1-06 adds the workflow start.
  - `decisions.assess_blocking_impact` on review_item.added. It calls `decisions.api.assess_blocking_impact(item_id, *, providers=None, clock=None)`: `decide(BLOCKING_IMPACT, …)` with subject `review_item`, then writes jev_factor and decision_id through a tasks.api setter. Nothing is written when Decisions is down (the factor stays 1.0).
- Kinds to register: `low_confidence_label` and `estimate_outlier` (tasks), `provisioning_failed` (agents; actions accept, reject, snooze; scope project). `decision_unavailable` exists already; add `action_payloads={"edit": {value}}` to it.
- Migration `tasks_0004` (after tasks_0003, phase expand) adds `jev_factor double precision NULL` and `decision_id uuid NULL`. If P2-13 (#68, which also uses tasks_0004) merges first, merge main and re-chain this revision as tasks_0005.
- Routes: `GET /v1/review` (session; paginated; `kind` query) and `POST /v1/review/{item_id}/decide` (session; idempotent; body {action, payload?, snooze_until?, version}). `/count` and `/kinds` already exist.

Frontend: the `/review` route renders `components/review/ReviewQueue.tsx`, with a generic item renderer that has per-kind slots.
- Each item is an `article` named by its target_title.
- The first item, or the one in `?item=`, takes focus. It carries aria-current="true" when chosen by `item`.
- The shortcuts region is labelled "Keyboard shortcuts" and shows Enter, o, e, r, s, j and k.
- Keys:
  - Enter: the primary action.
  - e: edit. For a label, it opens a 1/2/3 picker (Human/AI/Hybrid) and posts {label}.
  - r: reject.
  - s, then 1, 3 or t: snooze until +1 h, +3 h, or tomorrow's working-day start, through `snoozeUntil` in the new `lib/time.ts` (working hours from `settingsGetWorkingHours`, zone from the workspace settings).
  - o: open the target. A task goes to `/projects/{project_id}?task={id}`.
  - j/k and the arrow keys move between items.
- After a decision, focus moves to the next item.
- The heading shows "N to review" from `tasksGetReviewCount`.
- The list is `list` "Review queue" with class flex-col. Buttons have `min-h-11` and read Accept, Edit, Reject, Snooze and Answer.
- live-map: add `tasksListReview` to review_item lists.
- Run `make gen` for the client.

## Deviations (so far, to report in the PR)

- `blocking_impact` stays `text` (P0-18 made it text). squawk bans changing-column-type in expand revisions, so the value is stored as a float's text and cast in SQL.
- `jev_factor(answer, route)` in tasks/rules takes a structural `ScoreLike` and the route's string value, because tasks cannot import decisions (decisions imports tasks).
- `.importlinter`: one test-only ignore, `tumnis.modules.tasks.tests.** -> tumnis.modules.decisions.api` (module-graph-acyclic), for T-05 and T-06.
- The `plan_issue` kind is not registered: P1-11 (planning, plan_issues table) is unmerged and its accept/edit effects need that table. The generic renderer shows it once P1-11 registers it.
- `provisioning_failed` retry is a seam: it flips the profile status only. P1-06 adds the provisioning workflow start.
- Re-asking `decision_unavailable` on accept is not implemented: the inputs are not stored. That is left for P1-07.

## Scott items

None yet.

## Context7 docs checked

- Hypothesis (/websites/hypothesis_readthedocs_io_en): `run_state_machine_as_test`, `RuleBasedStateMachine`, `@invariant`, and settings (`stateful_step_count`). Used in T-P1-13-08.
- Still to check before writing the code that uses them: TanStack Router search params and navigate, TanStack Query invalidation, SQLAlchemy keyset over an expression.

## Verify commands

- Unit: `cd backend && uv run pytest -q -m "not integration and not contract" tumnis/modules/tasks/tests/unit -p no:randomly`
- Integration (bare, from the worktree root): `make test-int`, or rely on CI.
- Frontend: `cd frontend && npx vitest run src/components/review --maxWorkers=2`
- `make check`, with `SEMGREP_SETTINGS_FILE`, `SEMGREP_LOG_FILE` and `SEMGREP_VERSION_CACHE_PATH` under /tmp/claude-1002/semgrep/.
- Git: use `/usr/bin/git` only. Push with `/usr/bin/git push origin HEAD:wp/P1-13`.
