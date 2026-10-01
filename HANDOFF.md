# P4-02 handoff (Stuck handling, FR-10.5)

Branch `wp/P4-02` (pushed). No PR yet. Agent context c1 (c0's context and scratch were lost in the ENOSPC incident; the design was re-derived).

## Commits
- `0f4a28f9` test(agents): P4-02 spec tests (red): T-P4-02-01..07 (`backend/tumnis/modules/agents/tests/integration/test_stuck.py`, helpers `_stuck.py`), T-P4-02-04 (`backend/tumnis/modules/tasks/tests/unit/test_stuck_rule.py`), T-P4-02-09 (`frontend/src/components/focus/StuckPanel.test.tsx`, `test.fails`), plus interface stubs.
- `a1b9bbbb` merge origin/main (brings #142 P4-01: focus_0002, FocusBar changes).
- `7b1e351d` feat(agents): P4-02 stuck handling backend (wip). ruff, mypy and frontend `tsc` pass. Unit tests and `make check` have NOT been run on it yet, and integration NOT at all.

## Spec test status
- T-P4-02-04: green, marker removed.
- T-P4-02-01, 02, 03, 05, 06, 07: implemented, still `xfail(strict=True, reason="spec:P4-02")`. Not run yet. Next: run via CI (Docker rule) or one local `make test-int`, then remove each marker once that test passes.
- T-P4-02-09 (StuckPanel): not implemented. `StuckPanel.tsx` doesn't exist yet.
- T-P4-02-08 (skill cases): not started (see below).
- A4.2: no acceptance test on main (no P4-00 suite). Skipped; say so in the PR.

## What is built (7b1e351d)
- `tasks/rules.py`: `STUCK_MAX_MINUTES=10`, `StuckRefusal`, `stuck_step_refusal()` (422 `stuck_step_parent`, 409 `stuck_step_taken`, 422 `stuck_step_too_long`).
- `tasks/api.py`: `StuckRun`, `register_stuck_run_hooks(lookup, made)`. `create_task` checks the rule before the run counter and calls `made` after insert. `post_result(..., keep_status=False)`: a stuck run's result does not move the task to In review.
- `agents/migrations/0009_stuck_requests.py` (revision `agents_0009`, down `agents_0008`). Model `StuckRequest`. States: working, split, took_step, fallback. Unique `(workspace_id, focus_event_id)`.
- `agents/api.py`, "Stuck handling" section:
  - constants `STUCK_DEADLINE_S=60`, `STUCK_PRIORITY=1`, `RUN_PRIORITY_NORMAL=10`, `STUCK_TOPIC`, `LIVE_FOCUS="focus"`;
  - `configure_stuck` / `stuck_deadline_s` / `stuck_workflow_id`;
  - `request_stuck_run` (calls `request_run(..., RunKind.STUCK, priority=1)`). It is inert on `run_already_active` and falls back at once on any other refusal or when the profile's health is unreachable;
  - `mark_stuck_fallback`, `_stuck_answered` (emits `stuck.resolved` and marks focus live), `stuck_reported`, the tasks hooks, and `next_step(s, start, end)` with `NextStepOut` / `StuckStepOut`.
  - `accept_result`: stuck runs keep the task's status. A result review item is made only when the run took the step (`stuck_reported` returns True), not after a split.
- `agents/payloads.py`: `StuckResolvedV1` (`stuck.resolved`), with schema `schemas/events/v1/stuck.resolved.json` and the generated contract test.
- `agents/workflows.py`:
  - `handle_stuck` workflow on HUMAN_QUEUE, id `stuck:<focus event>`, with steps `request_stuck_run_step` and `mark_fallback_step`, then `recv(STUCK_TOPIC, stuck_deadline_s())`;
  - `start_stuck()`;
  - `start_dispatch` and both enrich enqueues now pass `priority=RUN_PRIORITY_NORMAL` (see deviations).
- `agents/events.py`: `agents.start_stuck` (focus.event kind stuck, direct=True) and `agents.deliver_stuck_outcome` (stuck.resolved). `signals.deliver_stuck_outcome`.
- `packet_builder.py`:
  - SKILLS[STUCK] is now "stuck" (it was "unstick");
  - a stuck packet gets `policy.max_tasks_per_run=1`, plus body `recent_comments` (last 5 comment blocks) and `stuck_step {max_minutes: 10}`;
  - other kinds exclude those fields, so the golden packets don't change; `schemas/packet/v1/task_run_request.json` was regenerated.
- `focus/api.py`: `FocusCurrentOut.next_step` (from `agents.next_step`). The stuck fire's dedupe key is now `stuck:{event.id}`, so there is one stuck event per check-in and a second tap repeats it.
- `settings.py`: `AgentsSettings.stuck_deadline_seconds=60`, applied in `worker.configure_agents`.
- Regenerated `schemas/openapi.json` and `frontend/src/api/*`. `frontend/src/test/msw/focus.ts` `quietFocus()` now has `next_step: null`.

## Remaining steps
1. Run `bash $TMPDIR/P4-02-c1/check.sh`. It is `make check` with the semgrep env vars pointed at scratch; recreate it if scratch is gone. Fix whatever fails. Likely candidates:
   - a meta test listing subscribers, events or tables;
   - `core/tests/integration/row_factory.py` may need a `("stuck_requests", ...)` entry;
   - the docs, ADR or event catalog may need `stuck.resolved`.
2. Push, open the PR (title `[P4-02] impl: stuck handling`), let CI run the integration layer, and remove each T-P4-02-0x marker as its test passes. Never edit assertions.
3. Frontend:
   - `frontend/src/components/focus/StuckPanel.tsx`: `role="status"`, `aria-label="Next step"`.
     - working: "Working on a first step…";
     - split: the step title and "`N` min";
     - took_step: the summary;
     - fallback: the first action with a 10-minute timer.
   - Render it in `FocusBar.tsx` with one line, when `data.next_step` is set.
   - Remove the `test.fails` from StuckPanel.test.tsx.
4. Skill (decision 61 approved):
   - commit `test(profiles): T-P2-12-11 lists the P4-02 stuck skill (decisions 57/61)` adding `("project-template", "stuck")` to SKILLS in `profiles/harness/tests/test_coverage.py`, with no assertion change;
   - write `profiles/project-template/skills/stuck/SKILL.md`;
   - cases `profiles/tests/cases/stuck/stuck_split.yaml` (Human task: one create_task under the task, label human, estimate between 1 and 10, then post_result) and `stuck_take_step.yaml` (an AI-able step: no create_task, post_result done with a summary), each with `meta.xfail: "spec:P4-02"`;
   - packet specs in `profiles/harness/packets.py`: extend TaskSpec with kind/extra for stuck, then `uv run python -m harness packets`;
   - a hostile `BASES` entry `"stuck"` in `profiles/harness/hostile.py`, then `uv run python -m harness index --suite hostile`;
   - bump `profiles/project-template/VERSION` and the matching `distribution.yaml` version.
   - If P2-16 merges first with its own SKILLS lines, keep both sides.
5. PR body:
   - Spec changes (decision 61);
   - deviations (below);
   - the Context7 citation (DBOS queue tutorial: "Workflows without assigned priorities have the highest priority");
   - per-layer results;
   - tell the coordinator the PR number for the label.

## Deviations (for the PR body)
- Priority: DBOS 3.1.0 gives an unprioritised enqueue priority 0, which sorts first, so STUCK_PRIORITY=1 alone would not jump the queue. Normal dispatches and enrich runs in the project partition now enqueue with RUN_PRIORITY_NORMAL=10.
- Outcome delivery: the plan has create_task/post_result calling DBOS.send. The repo rule (signals.py) is that the api never calls DBOS. The hooks write `stuck_requests` and emit `stuck.resolved`, and a worker subscriber sends to `stuck:<event>`.
- A stuck run's result keeps the task's status (no In review). A result review item is made only when the agent took the step itself. Scott item: what accept/reject of a stuck result should do. Today `_apply_result_decision` returns early because the task isn't In review.
- The stuck focus event's dedupe key is now per check-in (`stuck:{event.id}`), which makes REL-2 hold regardless of timing. `handle_stuck` is also inert when a stuck run of the task is already active.
- No `add_comment` MCP tool exists. T-P4-02-03's "scripted comment" is a run log line (stream) before the result.
- `handle_stuck` takes `requested_at` and `actor` beyond the plan's signature, so the run is requested as the person who tapped.

## Scott items
- Accept/reject semantics for a stuck run's `result` review item (above).
- "Tried once for real on the homelab with the live project agent" (done checklist): homelab only.

## Verify
- `cd backend && uv run pytest -q -m "not integration and not contract" tumnis/modules/tasks/tests/unit/test_stuck_rule.py`
- `make test-int` (bare, from the worktree root; at most once) or CI.
- `cd frontend && npx vitest run src/components/focus/StuckPanel.test.tsx`
- Use `/usr/bin/git`, and push with `/usr/bin/git push origin HEAD:wp/P4-02`.
