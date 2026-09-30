# HANDOFF · P1-08 Enrichment by the project agent

Stopped on "HANDOFF NOW" from the context watcher. Branch `wp/P1-08` (pushed), based on main
68d5476 (P1-06, P1-03, P1-07 merged). **No PR yet**, so no CodeRabbit threads and no CI run
of the implementation. Task prompt: `/tmp/claude-1002/coord7/p1-08.txt`.

## Commits

- `24100b6` test(agents): P1-08 spec tests (red)
- this file: chore: P1-08 handoff

## State of the spec tests (all red, strict expected failures)

| ID | Where | State |
| --- | --- | --- |
| T-P1-08-01 | `backend/tumnis/modules/agents/tests/unit/test_missing_fields.py::test_missing_fields_table[case]` (+ 4 helper tests in the same file, also `spec:P1-08`) | XFAIL locally (make check) |
| T-P1-08-02..14 | `backend/tumnis/modules/agents/tests/integration/test_enrich.py` | not run locally (Docker); expected XFAIL |
| T-P1-08-15 | `frontend/src/components/common/FirstAction.test.tsx` | expected fail, confirmed |
| T-P1-08-16 | `frontend/src/components/project/drawer/TaskDrawer.test.tsx` (appended; T-P0-24-16 untouched) | expected fail, confirmed |

Red-phase seams to replace (all raise `NotImplementedError("P1-08")`):
`agents/rules.py` (end: `TaskSnapshot`, `missing_fields`, `needs_enrichment`,
`merge_enrichment`, `plausibility_flag`), `agents/api.py` (end: `configure_enrichment`),
`agents/workflows.py` (end: `start_enrichment`), `frontend/src/components/common/FirstAction.tsx`
(`TaskFirstAction` renders null).

Shared/helper files (not locked): `backend/tests/fakes/fake_runner.py` (TASK_ID_SENTINEL
substitution from `run.packet.body.task.id`; `script(..., gate=threading.Event)`),
`backend/tests/fakes/recordings/runner/enrich_{ok_human,ok_hybrid,estimate_for_ai}.result.json`
(A1.4's `_phase1.runner_result` reads that path), `agents/tests/integration/_enrich.py`.

`make check`: everything green except `tests/meta/test_security_job.py::test_semgrep_rules_pass_their_own_tests`,
the known local-only failure (read-only `~/.semgrep`; set `SEMGREP_SETTINGS_FILE`,
`SEMGREP_LOG_FILE`, `SEMGREP_VERSION_CACHE_PATH` under `$TMPDIR/P1-08-c1/` to run it; the
sandbox refused my compound form, so run it as separate plain commands).

## Design decided (follow it; the tests encode it)

Interfaces the tests call:
- `agents.rules.TaskSnapshot(id, project_id, title, label, label_source, status, first_action,
  first_action_source, acceptance_criteria, estimate_minutes, version, enrichment_status=None)`
  (pydantic, frozen). rules.py may import only stdlib/pydantic/core.types (meta-test
  `tests/meta/test_boundaries.py` RULES_ALLOWED): no `core.limits`, no `skill_io`, no decisions.
- `missing_fields(t) -> list` in order first_action, acceptance_criteria, estimate_minutes:
  first action when blank or source `placeholder`; criteria when blank; estimate when label
  human/hybrid and None. `needs_enrichment = bool(missing) and status != "done"`.
- `merge_enrichment(current, res, *, requested, estimate_range) -> EnrichmentPatch`
  (first_action, acceptance_criteria, estimate_minutes, label, label_reason). Fill only
  requested AND still missing now. Criteria text: `- <line>` per criterion; for hybrid add
  `AI part: <ai_portion>` and `Your part: <human_portion>` lines after them (only when the
  criteria are filled by this enrichment). Label revision only when current label is None or
  label_source in {jev, fallback} (never user/agent); when the revised label is human/hybrid
  and the task has no estimate, the estimate applies even if not requested. Estimate only for
  an effective human/hybrid label and inside `estimate_range` (caller passes
  `skill_io.ESTIMATE_RANGE`).
- `plausibility_flag(answer_with_.score | None, route: str)`: only route `"applied"`;
  score <= 0.5 too_low, >= 3.5 too_high.
- `skill_io.EnrichTask.label` must become `Label | None` (T-01 builds a request with a
  pending label; the enrich workflow proceeds with a NULL label after the label wait).
  Regenerate `schemas/enrichment/v1/request.json` with `make gen`.
- `agents.api.configure_enrichment(*, clock=None, label_wait_s=10.0, run_timeout_s=120)`:
  called with nothing restores defaults; `clock` is what `agent_for_project(now=...)` uses
  (tests pass the FixedClock; heartbeats are stamped by the app's clock).
- `agents.workflows.start_enrichment(workspace_id, task_id, project_id, *, key)`: enqueue
  `enrich_task` on `RUNS_QUEUE` with `SetWorkflowID(f"enrich:{task_id}:{key}")` and
  `SetEnqueueOptions(queue_partition_key=str(project_id))`, inside
  `asyncio.create_task(..., context=contextvars.Context())` exactly like `start_provision`
  (subscribers run inside a DBOS step). **Deviation for the PR body:** the plan's dedup id
  `enrich:{task_id}:{version}` is replaced by the deterministic workflow id
  `enrich:{task_id}:{event_id}` (DBOS 3.1.0: partition keys and deduplication ids cannot be
  used together — Context7 /dbos-inc/dbos-docs queue tutorial "Partitioning Queues"; a
  workflow id is an idempotency key — workflow tutorial "Workflow IDs and Idempotency";
  `TaskUpdatedV1` carries no version).

Workflow `enrich_task(workspace_id: str, task_id: str, only: list[str] | None = None)`:
load (NotFound/not needed -> return) and set `enrichment_status = pending`; placeholder step
(`decisions.generation_api.placeholder_first_action`, only `agents.workflows` may import it)
written by `tasks.set_placeholder_first_action` only if first action still empty, then
status `running`; wait for label with `DBOS.sleep_async(0.5)` loop up to `label_wait_s`;
`agent_for_project` -> `agent_offline` / `not_provisioned`; build request
(`packet_builder.enrichment_request`: brief via `knowledge.api.get_brief`, "" on NotFound;
`tasks.estimate_history` 10 newest, titles cut to 120); run_id deterministic
(`uuid5(NS, DBOS.workflow_id)`) so a replay after a kill writes no second mailbox row; call
`run_skill` as a child workflow (child id is derived from the parent's; `runs.workflow_id`
comes from `DBOS.workflow_id` in `hermes._workflow_id`, i.e. the child — correct);
`TaskPacket(kind="enrich", skill="enrich", output_schema=SchemaRef("enrichment","result",1),
timeout_s=run_timeout_s, prompt_text=render_prompt(...), body=req)`; validate (schema + 
`enrichment_errors`; failure -> status `failed`, runs row keeps its error); plausibility via
`decisions.api.decide(DecisionPoint.ESTIMATE_PLAUSIBILITY, ...)` subject task, and on a flag
`tasks.add_review_item("estimate_outlier", ...)` (kind already registered by P1-13 in
`tasks/review.py`, payload `EstimateOutlierPayload`; its edit is applied by
`tasks.apply_review_decision`); apply via `tasks.apply_enrichment`; status `done`.
Plausibility runs on the estimate being applied; decisions down routes `deterministic`
(no `decision_unavailable` item for this point).

tasks module:
- migration `tasks_0007` after `tasks_0006` (check no open PR adds one: open PRs are #102,
  #103, #104; P2-02 #104 touches tasks/api.py but adds no tasks migration):
  `first_action_source text NULL CHECK IN ('placeholder','agent')`,
  `enrichment_status text NULL CHECK IN ('pending','running','done','agent_offline','not_provisioned','failed')`,
  `phase = "expand"`. Mirror in `tasks/models.py`; add both to `TaskOut`; `make gen`.
- `update_task` / `create_task`: writing `first_action` clears `first_action_source` (T-10).
- `set_placeholder_first_action(s, task_id, text)`: only while first action empty; source
  placeholder; bumps version (Scott decision 28 pattern); no `task_changes` row; `task.updated`.
- `apply_enrichment(s, task_id, patch, *, run_id)`: one versioned update, first_action_source
  `agent`, label_source `agent` when revised, one `task_changes` row by SYSTEM_ACTOR;
  `first_action_source` rides along with `first_action` in before/after like `_LABEL_META`
  (do NOT add it to `rules.UNDO_FIELDS`); `undo_task` restores it. `task.updated` with
  changed_fields.
- `set_enrichment_status(s, task_id, status)`: no version bump, `mark_changed` only.
- `estimate_history(s, project_id, *, limit=10)`: P2-02 (#104) adds a function of the same
  name returning `EstimateSample(task_id, label, estimate_minutes, actual_minutes)`. Write a
  superset (same class name, plus `title`) so whichever merges second takes it.
- `get_task`: also answer `change_id` for enrichment writes (first_action_source `agent` or
  label_source `agent`), additively (T-11 and the drawer's Undo read it).
- `agents/adapters/hermes.py` dispatch: set `runs.task_id` from `packet.body["task"]["id"]`
  (T-02 asserts it).

Subscribers (`agents/events.py`, may import workflows like decisions/events.py does):
- `agents.enrich_on_create` (`task.created`): `start_enrichment(..., key=str(event_id))`.
- `agents.enrich_on_update` (`task.updated`): only when "label" in changed_fields, the task
  is human/hybrid without an estimate, and `enrichment_status` is terminal (not NULL, not
  pending/running); requests the estimate only (`only=["estimate_minutes"]`). This avoids the
  re-trigger loop on the enrichment's own writes, the create-vs-Jev-label race, and
  re-enrichment after an undo.

Frontend: `TaskFirstAction({taskId})` reads `taskQueryOptions`; `data-testid="first-action"`,
`data-state` placeholder | pending | agent | user; pending text `First action pending`; the
placeholder's text is exactly the placeholder (A1.1 uses `toHaveText`). TaskDrawer: first
action, a list `aria-label="Acceptance criteria"` of `- ` lines, estimate (`20 min` /
`No estimate`), and when `first_action_source === "agent"` and `change_id`: `Enriched by
agent` + button `Undo` posting `/v1/tasks/{id}/undo {change_id, version}` (reuse the undo
mutation in `lib/undo.ts`/`project/mutations` if it fits) and showing the answer.

## Remaining steps (TDD order)

1. T-01 rules + `EnrichTask.label` nullable; unmark; commit `feat(agents): ...`.
2. Migration + models + TaskOut + `make gen`; T-02 happy path (workflow, subscriber,
   `configure_enrichment`, placeholder, apply, hermes task_id); unmark each as it passes.
3. T-03/04/12 merge + label revision; T-05/06 placeholder; T-07 availability; T-08 request
   builder; T-09 plausibility; T-10/11 field protection + undo; T-13/14 failure + kill.
4. T-15/16 UI; `npm --prefix frontend run typecheck`.
5. Refactor: `packet_builder.enrichment_request(task_id)`; keep P2-02's `build_packet` for
   P2-02 (it adds one; don't add another — note it in the PR).
6. `make check`, then bare `make test` and `make test-int` (Docker only as bare commands from
   the worktree root; or rely on CI when the VM is loaded).
7. Push `/usr/bin/git push origin HEAD:wp/P1-08`, open the PR (body file in `$TMPDIR/P1-08-c1/`,
   deviations + Context7 citations + Scott items, ending with the Claude Code line), then
   `gh pr comment <url> --body "@coderabbitai review"`; run the review loop; send
   "#<PR> MERGE-READY at <sha>" to main.

## Scott items

1. T-P1-08-04: the plan stores the Hybrid split in "the task's description", but tasks have
   no description column (FR-3.1). Chosen: `AI part:` / `Your part:` lines after the
   acceptance criteria, written only when the enrichment fills the criteria. Confirm or ask
   for a description column.
2. A1.1 (Playwright) and A1.4 step 2 stay red: the seed has no `Acme site` / `Beta app`
   projects or agent profiles, the `fake_runner` factory has no `.offline()` / `.script()`,
   and A1.1 needs a fake runner scriptable over REST in the compose stack. That is phase 1
   seed/test-stack infrastructure no merged WP shipped; leave `spec:P1-08` on A1.4 step 2.

## Verify

```bash
cd backend && uv run pytest -q -m "not integration and not contract" tumnis/modules/agents/tests/unit/test_missing_fields.py
make check          # from the worktree root
make test-int       # bare, from the worktree root (Docker)
npm --prefix frontend run test -- --run src/components/common/FirstAction.test.tsx src/components/project/drawer/TaskDrawer.test.tsx
```
