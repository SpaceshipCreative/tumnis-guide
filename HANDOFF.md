# P4-01 (Guardrail level): handoff

Branch: `wp/P4-01` (push with `/usr/bin/git push origin HEAD:wp/P4-01`). No PR yet.
Instructions: ~/tumnis-coordinator/prompts/wave1/P4-01.txt (read it whole, including its restart notes).

## Done

- `01a487f test(focus): P4-01 spec tests (red)`. `make check` was green: backend, daemon and profiles
  passed in a full run, then frontend lint, typecheck and the full Vitest run (252 passed, 4 expected
  failures) after the stub lint fix. The semgrep meta test needs
  `SEMGREP_SETTINGS_FILE` / `SEMGREP_LOG_FILE` / `SEMGREP_VERSION_CACHE_PATH` under `/tmp/claude-1002/P4-01-c0/semgrep/`
  (~/.semgrep is read-only).
- The red commit holds these tests as strict expected failures, all `spec:P4-01`:
  - Unit: T-01 `tests/unit/test_levels.py`; T-02 and T-03 `tests/unit/test_guardrail_rules.py`.
  - Integration: T-04, T-05 (parametrised return/stay) and T-06 in `tests/integration/test_detour.py`; T-07 in `test_prepare_next.py`; T-08 in `test_guardrail_block_end.py`. They share the helpers in `tests/integration/_guardrail.py`.
  - Vitest: T-09 `components/dashboard/GuardrailDashboard.test.tsx`, T-10 `lib/prefetch.test.ts`, T-11 appended to `machines/focusSession.test.ts`, T-13 appended to `components/focus/FocusBar.test.tsx`. MSW helpers are in `test/msw/guardrail.ts`.
  - Stubs (raise or no-op): the `rules.py` guardrail functions (`PlanItemLite`, `TaskLite`, `captures_detour`, `current_guardrail_task`, `next_guardrail_task`, `is_detour`) and `lib/prefetch.ts`. `focusSession.ts` has only the new event types (`LEVEL`, `SWITCH_DETOUR`, `RETURN_PROMPT`, `RETURN`, `STAY`) and context keys (`guardrail`, `returnToTaskId`). setup() was left unchanged so R-38 (`machines.test.ts`) stays green.
- Sent main a scope message. The coordinator approved all of it:
  1. Defer T-P4-01-12 and the master focus SKILL.md change to P2-16; use the plan's fixed-template fallback. List both in the PR body as P2-16 follow-ups.
  2. No A4.1 in this PR (it isn't on main, and SEED owns its seed data).
  3. Build the previous task's return to Today as in_progress -> backlog -> today in one transaction. Never touch tasks/rules.py. The coordinator's condition: check whether the backlog step produces extra events, audit rows or digest entries a user would see. If it does, suppress them or list it as a Scott item. (P→B and B→T each emit `task.status_changed`. Check the P2-03 digest and the activity feed.)
  4. Approved deviations: the `register_enrichment_starter` seam in agents.api, and the packet prefetch.

## Remaining TDD steps (remove one marker at a time; commit after each green)

1. **Rules** (`focus/rules.py`; full coverage is required):
   - `captures_detour(level) = level == "guardrail"`.
   - `current_guardrail_task`: an In progress task whose item is in the plan wins. Otherwise take the first accepted item by position whose task exists and is not `done` or `waiting_on_human`. Otherwise None.
   - `next_guardrail_task(plan, tasks, current)`: the first eligible accepted item after `current`'s position. None when `current` is None or not in the plan.
   - `is_detour(t, today) = t is None or t not in today`.
   - Then unmark T-01, T-02 and T-03.
2. **Migration `focus_0002`** (down `focus_0001`, `phase="expand"`, real downgrade):
   - Add to `focus_events`: `detour_task_id` UUID null, `return_to_task_id` UUID null, `return_decision` text null with CHECK IN ('return','stay') added NOT VALID (squawk), and `decided_at` timestamptz null.
   - Mirror the columns in `models.py`.
   - Run `uv run alembic heads` first; focus head was focus_0001.
3. **Payload**: `FocusEventV1` gains `detour_task_id: UUID | None = None` and `return_to_task_id: UUID | None = None`. Then run `make gen` and commit the generated outputs (schemas, openapi, TS client).
4. **api.py**:
   - `DetourIn{title 1..200, project_id}`. `RespondIn.detour: DetourIn | None = None`, with a model_validator: a detour needs response "switched" and excludes `to_task_id` (422).
   - `ReturnIn{decision: Literal["return","stay"], version}`. `version` is the DETOUR TASK's version; Return moves the detour with it, so a stale one is 409 stale_version.
   - `FocusCurrentOut` gains `guardrail: GuardrailOut | None = None` (`{current_task_id, next_task_id, remaining}`, set only when the effective level is guardrail; `remaining` counts the eligible items other than current) and `detour: DetourOut | None = None` (`{event_id, detour_task_id, detour_title, return_to_task_id, return_to_title, message, rule}` for the latest switched event with a detour and no `return_decision`).
   - `respond()` at Guardrail with a detour:
     - Below Guardrail: 409 `detour_needs_guardrail`.
     - Create the task with `tasks.create_task(s, ctx.actor, TaskCreate(project_id, title), now=now, source="detour")`. The label stays None, so Jev labels it via P1-07.
     - The previous task is the open session's task (fall back to `event.task_id`). If it is In progress, move it P→B→T.
     - Move the detour B→P.
     - Fire `switched` with dedupe key `f"switched:{detour_id}:{now.isoformat()}"`. This is the SAME key `start_session` uses, so the plain switched event dedupes and T-04's "exactly one switched" holds. Pass `rule="Guardrail · detour"` (stored rule text; see deviations), `message=f"Captured. Back to {prev.title}?"`, and the detour and return ids in the row and payload.
     - Also record the response as P2-15 does.
     - `_fire` needs optional `rule=`, `detour_task_id=` and `return_to_task_id=` parameters.
   - `return_detour(ctx, body, now, session)`, exposed as `POST /v1/focus/return` in router.py with `RoutePolicy(auth="session", idempotent=True)`. It locks the open detour event (none: 409 `no_open_detour`).
     - Return: the detour P→B with `body.version`, then the previous task T→P if it is Today.
     - Stay: nothing moves.
     - Set `return_decision` and `decided_at`, then `mark_changed`.
   - Then unmark T-04, T-05 and T-06, and T-08 (only needs the `guardrail` field).
5. **prepare_next**:
   - `agents/api.py`: `register_enrichment_starter` and `enrich_ahead(ctx, task_id, project_id, key)`. Gate on `agent_for_project(...) != "not_provisioned"` (follow the `register_provision_starter` pattern). `agents/workflows.py` calls `api.register_enrichment_starter(start_enrichment)`.
   - `focus/events.py`: a new subscriber `@subscribe("task.status_changed", name="focus.prepare_next")`. On `to == in_progress` at effective Guardrail, it reads today's plan and task statuses, computes `next_guardrail_task(current = the started task, or the rule's current)`, and calls `agents.enrich_ahead(..., key=f"prepare_next:{event_id}")` when the next task has no first_action.
   - Then unmark T-07. That test uses the fake_runner fixture and an agent profile set to 'ready'.
6. **Frontend**:
   - `lib/levelRules.ts`: mirror of the rules plus `formatRule`.
   - `prefetch.ts`: `prefetchQuery` on `taskQueryOptions(id)` and `agentsGetTaskPacketOptions({path:{task_id}})`, both with `staleTime` 60 s. Use the same options in the card's `useQuery` so it does not refetch (T-09 asserts a packet count of 1).
   - Machine: `isGuardrail` guard; `setLevel`, `postDetour`, `postReturn` and `postStay` actions; `returnPrompt` delay of 120000; states `detour` and `returnPrompt` (see the T-11 test for the exact transitions; TASK_STARTED in those states only runs startTimer). Every name must be declared in setup and used in the config (R-38).
   - `GuardrailDashboard.tsx` (`data-testid="today-item"` card, "N more today", Start/Done with the task version from the task query, linked context from `packet.body.context_items` rendered as text with the untrusted-data tags stripped, "Show everything for now" → POST /focus/less).
   - `DashboardPage` switches on `focusGetCurrent.level === "guardrail"`. Add the focus current read to the route loader in `routes/index.tsx` so first render has data.
   - FocusBar: a standing "Less of this" only at guardrail outside checkIn (posts `{}`); Switched at guardrail opens `SwitchPicker.tsx`; `ReturnPrompt.tsx` (Return/Stay, "Guardrail · detour", auto Stay after 2 minutes).
   - Update `quietFocus()` in `test/msw/focus.ts` if `make gen` makes the new fields required (they default to None, so they should stay optional).
   - Unmark T-09, T-10, T-11 and T-13.
7. **Finish**:
   - Run `make check`; the unit layer with `-n 3`; and `make test-int` once at most (DOCKER LOAD RULE: prefer CI).
   - Push, write the PR body (deviations below, Context7 citations: TanStack Query v5 prefetching docs, XState setup/delayed transitions), then run `gh pr create --title "[P4-01] impl: guardrail level"` and `gh pr comment <url> --body "@coderabbitai review"`.
   - Follow the review loop until clean, then SendMessage main "#<PR> MERGE-READY at <sha>".

## Deviations to list in the PR

- T-P4-01-12 and SKILL.md are deferred to P2-16. The return question uses the fixed template "Captured. Back to <task>?".
- A4.1 is not in this PR.
- The P→B→T build-around, because there is no in_progress→today edge (tasks/rules.py is untouched).
- The agents.api enrichment-starter seam (not in the plan's Files list).
- The prefetch covers the task plus its packet, because there is no GET context-items route.
- The stored rule text is "Guardrail · detour" (the attribution format P2-15 stores and A4.1 shows) rather than the plan's id-style "guardrail.detour".
- tasks/api.py is unchanged: `create_task` already takes `source=` and there is no CHECK on `tasks.source`.
- `event_id` stays required on respond.

## Verify

```
cd backend && uv run pytest -q -m "not integration and not contract" tumnis/modules/focus
cd frontend && npx vitest run --maxWorkers=4 src/machines src/lib/prefetch.test.ts src/components/dashboard src/components/focus
make test-int   # bare, from the worktree root, once
```
