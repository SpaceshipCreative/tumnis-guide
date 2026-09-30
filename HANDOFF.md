# P1-07 handoff (Quick-add labels and overrides)

Stopped on the coordinator's HANDOFF NOW at about 350k tokens, right after the red commit. No PR is open yet.

## State

- Branch: local `wp/P1-07` (renamed from the throwaway branch; the rename was applied but the config write failed on the read-only `.git/config`, which is harmless). It is based on main at `79909cc`. Push with `/usr/bin/git push origin HEAD:wp/P1-07`.
- Commits:
  - `81ace5b` test(decisions): P1-07 spec tests (red)
  - (this file) chore: P1-07 handoff
- CI: none yet. No PR, no CodeRabbit threads.
- `make check` is green on the red commit. The one exception is `tests/meta/test_security_job.py`, which fails only locally because `~/.semgrep` is read-only. It passes when `SEMGREP_SETTINGS_FILE`, `SEMGREP_LOG_FILE` and `SEMGREP_VERSION_CACHE_PATH` point under /tmp/claude-1002.
- Unit red is confirmed: T-08 (7 cases) and T-14 xfail. Vitest red is confirmed: T-10..13 show "4 expected fail".
- Integration red (T-01..07, T-09) has NOT been run locally yet. These tests fail at call time on the missing `decisions.api.use_providers` and the missing columns, so they should xfail. Confirm with `make test-int` or in CI.

## Spec tests written (all red)

| ID | File |
| --- | --- |
| T-01 | backend/tumnis/modules/decisions/tests/integration/test_label_latency.py (slow; real relay via `relay_running`) |
| T-02, 03, 04, 09 | backend/tumnis/modules/decisions/tests/integration/test_label_task.py |
| T-05, 06, 07 | backend/tumnis/modules/tasks/tests/integration/test_override_label.py |
| T-08 | backend/tumnis/modules/tasks/tests/unit/test_label_rules.py |
| T-14 | backend/tumnis/modules/decisions/tests/unit/test_label_inputs.py |
| T-10..13 | frontend/src/components/common/LabelChip.test.tsx (stub `LabelChip.tsx` exports `TaskLabelChip`) |

Shared helpers live in `backend/tests/_labels.py`, outside `tumnis`, so tasks tests don't import decisions and break the import-linter cycle/api-only contracts: `label_answers`, `use_label_fakes`/`reset_label_fakes`, `relay_running`, `quiesce`, `until`, `owner_query`, `Gate`, `gated_jev`.

When implementing, remove the `# type: ignore[attr-defined]` on the red imports in test_label_rules.py and test_label_inputs.py (mypy will flag them as unused). That is not an assertion edit.

## Decisions taken (coordinator approved the plan)

1. **A1.1 label step:** Playwright can't reach it until the shared test-hooks PR lands (coordinator branch `fix/test-fakes-script-hook`: a Postgres-backed fake-script store and `POST /v1/test/fakes/{adapter}/script`) and P1-08 lands. Leave A1.1's `test.fail()` in place. Prove the label step at the API layer (T-01, T-02), in Vitest (T-10..13), and with A1.4 step 3 (`backend/tests/acceptance/test_a1_4_degraded_modes.py::test_jev_and_vllm_down_sends_label_to_review`, marker spec:P1-07, which is ours). Record this as a deviation in the PR body.
2. **Fake injection:** add a process-level override `decisions.api.use_providers(Providers | None)`. `decide(providers=None)` uses it before `_production_providers`, because `fakes["decisions.jev"]` is a different instance from `_build`'s cached fake. Every test resets it (autouse fixtures call `reset_label_fakes`).
   - Fix the adjustable helpers in `backend/tests/acceptance/_phase1.py`. `fail_decision_providers` calls `script_failure`, and `script_label` calls `script(answer=, confidence=)`; neither exists on FakeDecisions. Use `fakes[name].script("quick_add_label", fail=AdapterUnavailable(name, "ask", "down"))` plus `use_providers(...)`, and add a reset (an autouse fixture in a new `backend/tests/acceptance/conftest.py`, or reset inside the test's teardown) so the override does not leak.
3. **Enqueue from the subscriber:** the handler runs inside a DBOS step (`events.run_handler`). dbos 3.1.0 `_context.py::create_start_workflow_child` asserts it is not in a step, so `DBOS.enqueue_workflow_async` cannot be called there. Enqueue in a clean context instead: `await asyncio.create_task(_enqueue(...), context=contextvars.Context())`, with `SetWorkflowID(f"label:{event_id}")` (idempotent per event, which covers T-09) plus `SetEnqueueOptions(deduplication_id=f"label:{task_id}:{version}")`, suppressing `DBOSQueueDeduplicatedError`. The alternative is `deadletter.dbos_client()`, but the worker never configures that client.
4. **Queue:** register `decisions` in `backend/tumnis/worker.py::register_queues` with `limiter={"limit": 1200, "period": 60}` and `polling_interval_sec=0.1`. Context7 (/dbos-inc/dbos-transact-py) confirms both `register_queue` kwargs exist in 3.1.
5. **Review kinds:**
   - Low confidence goes to `low_confidence_label`. Use the same names as PR #80 (P1-13) in `tasks/review.py`: `LABEL_KIND`, `LowConfidenceLabelPayload {suggested: rules.Label, probabilities: dict[str,float] = {}, reason: str<=500 | None, decision_id: UUID | None}`, `LOW_CONFIDENCE_LABEL`, registered. Leave out `action_payloads`, which isn't on main. The merge overlap is textual.
   - `unknown` winner goes to a `decision_unavailable` item (decisions' own kind; its payload is `{decision_id, point}`, dedupe `decision:quick_add_label:<task>`), because #80's payload requires `suggested` to be a real label. T-04 asserts this.
6. **Migration:** a new tasks revision chained after `tasks_0004`. #80 adds `tasks_0005`, so use a distinct id (for example `tasks_0006`, file `0006_label_columns.py`) and re-chain after `tasks_0005` if #80 merges first. Columns: `label_reason text`, `label_confidence double precision`, `label_decision_id uuid` (no FK), `label_suggestion task_label`. `phase = "expand"`.
7. **Undo:** don't add `label_source` to `rules.UNDO_FIELDS`. The locked `test_undo_rules._row` has no `label_source`, so `undo_snapshot` would KeyError. Instead:
   - `set_ai_label` writes its own change row via `record_change(before={"label": None, "label_source": None}, after={...})`.
   - `undo_task` also restores `label_source` when the change's `before` holds it.
   - `_record` adds `label_source` when `label` changed.
   - `GET /v1/tasks/{id}` (`get_task`) returns `change_id` = the AI label change while `label_source in (jev, fallback)`, the change is not undone, and its `task_version == version` (T-13).
8. **API signatures:** `set_ai_label(s, task_id, *, label, source, reason, confidence, decision_id, now=None) -> UUID | None` and `set_label_suggestion(s, ...)`. They take `s: AsyncSession` like every tasks api function, which deviates from the plan's session-less signature. `set_ai_label` runs `UPDATE ... WHERE label_source IS DISTINCT FROM 'user'` and returns None if 0 rows. It emits task.updated with changed_fields `[label, label_reason, label_source]`, calls `mark_changed` (the /ws push), and sets `label_decision_id`, `label_confidence` and `label_suggestion=NULL`.
9. **Override (`update_task`):** when the label changes and the task had `label_decision_id` or `label_suggestion`, emit `human.decided`:
   - Payload: `{item_kind: "label_override", item_id: task, target_type: "task", target_id: task, decision: <label>, previous: {label, label_source, label_suggestion}, payload: {"value": <label>, "overridden": <label != AI value>}, decision_id}`.
   - Close open `low_confidence_label` items for the task as `decision = 'superseded'`, `decided_at = now`.
   - Extend `decisions/events.py::record_outcome` to use `payload["overridden"]` when present (T-05 needs overridden=true and final_value="human").
   - After #80 merges, its `tasks.apply_review_decision` calls `update_task` too, so skip the emission for that path (a flag or actor check). Flag this to the coordinator.
10. **Rules:**
    - Move `LabelSource` into `tasks/rules.py` (re-export it from api).
    - `may_auto_label(src) = src != "user"`.
    - `label_state(label, suggestion)`: confirmed, then suggested, then pending.
    - `decisions/rules.py::label_inputs(task: Mapping, *, parent_title, project: Mapping | None, reserved_judgments) -> dict` returns only title, parent_title, project_name, project_goal and reserved_judgments, and omits None or empty values.
11. **Workflow:** `decisions/workflows.py`:
    - `label_task(workspace_id, task_id, task_version)` workflow with steps `load` (task, parent title, project name and goal, setting `triage.reserved_judgments`), `decide` (`decisions.api.decide(QUICK_ADD_LABEL, ..., subject=task, project_id=...)`) and `apply`.
    - The reason comes from `rules.label_reason(label, companions)`, where companions are the noul values of the decision answers.
    - Subscribers in `decisions/events.py`:
      - `decisions.label_on_create` (task.created): skip when the payload label is not None.
      - `decisions.label_on_title_change` (task.updated): only when `title` is in changed_fields and `may_auto_label`.
    - Seed setting `triage.reserved_judgments = ["pricing", "hiring"]` (check `backend/fixtures/seed`).
12. **TaskOut:** add `label_reason` and `label_suggestion`, then run `make gen`. The MSW fakes in `frontend/src/test/msw/project.ts` may need the new fields (update factories, never assertions).
13. **UI:**
    - `LabelChip` (presentational) and `TaskLabelChip({taskId})` (useQuery `taskQueryOptions`, `useUpdateTask`, undo via `lib/undo`).
    - States: pending (`Labeling…`, `aria-busy`), suggested (`Suggested: Hybrid`, `border-dashed`, accessible name starting "Suggested"), confirmed.
    - Click opens a `listbox` named "Label" with options Human/AI/Hybrid; keys 1/2/3 pick inside it.
    - The reason is `aria-describedby` text.
    - Show an "Undo AI label" button when an undo entry for the task's AI change exists (remember it from the GET's `change_id` when the source is jev or fallback).
    - The chip has `data-testid="label-chip"` and `data-state`.
    - Add a session-local "Just added" list (listitems with title plus chip) in `quickadd/` (QuickAddHost `onCreated`). Show the chip in `project/TaskRow.tsx`. Keep changes local (DS-01 is restyling the shell).
14. **Recording:** the plan mentions `quick_add_label__low_confidence.json`; it's optional, because tests script the answers directly.

## Remaining TDD steps (plan order)

1. T-08, T-14: rules. Remove markers.
2. Migration, models, `set_ai_label`, workflow, subscriber, queue, `use_providers`: T-02.
3. T-03, T-04 (review routing), then T-09 (dedupe).
4. T-05, T-06: `update_task` extension, the `record_outcome` tweak, the conditional update.
5. T-07: title-change subscriber.
6. T-10..13: LabelChip, TaskRow, the quick-add "just added" row; `make gen`; fix the MSW fakes.
7. T-01: measure. If over budget, tune the queue polling, never the test.
8. A1.4 step 3: fix the `_phase1.py` helpers, then remove its spec:P1-07 marker.
9. `make check`, `make test`, `make test-int`, Vitest. Push, then open the PR:
   - title `[P1-07] impl: Quick-add labels and overrides`
   - the body cites Context7 DBOS docs and the dbos 3.1.0 source for the enqueue-in-step limit
   - the deviations above
   - Scott items: none so far
   - comment `@coderabbitai review`, then run the review loop and send `#<PR> MERGE-READY at <sha>` to main.

## Verify commands

- `make check` (set the SEMGREP_* env vars to files under /tmp/claude-1002 for the semgrep meta test)
- `cd backend && uv run pytest -q -m "not integration and not contract" tumnis/modules/decisions tumnis/modules/tasks -k label`
- `make test-int` (bare, from the worktree root)
- `cd frontend && npx vitest run src/components/common/LabelChip.test.tsx`
