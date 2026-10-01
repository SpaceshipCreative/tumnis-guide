# HANDOFF: P2-06 Delegation (continuation c0 → c1)

Stopped because the context watcher sent "HANDOFF NOW". No PR is open yet. Branch:
`wp/P2-06`, pushed with `/usr/bin/git push origin HEAD:wp/P2-06`. The local branch was
renamed from the throwaway branch, and `.git/config` is read-only, so pushing
`HEAD:wp/P2-06` is how it works.

## Commits (on top of main fb7d771)

- `bb2a09a` test(agents): P2-06 spec tests (red). T-P2-06-01 to 10 as strict xfails.
  - Unit tests: `backend/tumnis/modules/agents/tests/unit/test_delegation_rules.py`
  - Integration tests: `backend/tumnis/modules/agents/tests/integration/test_delegation.py`
  - Helper (no assertions): `backend/tumnis/modules/agents/tests/integration/_delegation.py`
- `7253beb` feat(agents): delegation rules in `rules.py`. T-P2-06-06, 08 and 10 are green and their markers are removed.
  - `MAX_DELEGATION_DEPTH`, `LOOP_REPEAT_LIMIT`, `LOOP_WINDOW`, `WaitStatus`
  - `DelegationRecord(delegation_id, task_id, delegated_at, accepted)`
  - `delegation_depth`, `depth_exceeded`
  - `is_delegation_loop(history, task_id, now, *, chain=())`
  - `wait_status`
- This handoff commit.

## Test status

- Green: T-06, T-08 and T-10 (unit).
- Still strict-red in `test_delegation.py`: T-01, 02, 03, 04, 05, 07 and 09.
- Still strict-red on main: A2.4 (`backend/tests/acceptance/test_a2_4_delegation_wait.py`). Remove its marker only after it passes, ideally in CI.

## Remaining TDD steps

Remove one marker at a time, in plan order: T-01, T-02, T-03 to 05, T-07 and 09, then A2.4.

1. **Migration `agents_0009`** (main's agents head is agents_0008; open PRs #134 and #132 add no agents migration). File: `agents/migrations/0009_delegations.py`, `down_revision="agents_0008"`, `phase="expand"`.
   - Create it with `create_tenant_table("delegations", ...)`.
   - Columns: `child_task_id uuid NOT NULL`, `project_id uuid NOT NULL`, `parent_run_id uuid NULL`, `depth int NOT NULL CHECK (depth BETWEEN 1 AND 2)`, `note text NULL`, `delegated_at timestamptz NOT NULL` (taken from the call's clock, never DB `now()`), `accepted_at timestamptz NULL`, `tainted bool NOT NULL default false`.
   - Index `(workspace_id, child_task_id, delegated_at)`.
   - The row's `id` is the delegation id, which is also the child run's id.
   - Mirror it in `models.py` (class `Delegation`) and update the docstring list.
2. **`api.request_run`**: add an optional `delegation_id: UUID | None = None`. When it is set, the run id is the delegation id and `runs.delegation_id` is set to it.
   - The workflow id then becomes the delegation id through `dispatch_workflow_id(run_id)` and `prepare_run`.
   - Add `DELEGATION_LOOP = "delegation_loop"` to `CLOSING_LINES` ("Stopped: it delegated the same task in a loop").
   - Do NOT add it to `CANCEL_LIMITS`: that would raise a `run_limit` item on top of the loop item.
3. **New file `agents/delegation.py`.** It imports api, and api must not import it (that would be a cycle). The plan's file table says api.py, but api.py is a hot shared file with #132 and #134; note this deviation in the PR. It holds:
   - Models: `DelegateBody(SurfaceInput)` with `task_id` and `note: str|None max 4000`; `DelegateIn(WriteInput, DelegateBody)`; `DelegateOut` with `schema_version`, `delegation_id`, `project_id`, `depth` and **`tainted: bool`**; `WaitIn(SurfaceInput)` with `delegation_id` and `timeout_seconds: int = Field(600, ge=1, le=600)`; `WaitOut` with `schema_version`, `status`, `run_status: RunStatus`, `question`, `result_summary` and `task_status: str`.
   - **`tainted` is required** by the locked taint sweep T-P2-08-07 (Scott decision 35). `delegate_task` counts as a create op there, and the master caller's output must be tainted, so set `tainted = call.tainted or task.tainted`. The child run's taint stays task-derived (from `request_run`). Say so in the PR.
   - `delegate_task(call, inp)`, in order:
     - `tasks.get_task` (404 if missing).
     - Check the project profile with `api._project_profile`, or write a small public helper. None → 409 `agent_not_provisioned`.
     - Walk the chain (below).
     - If `depth_exceeded(delegation_depth(chain))` → 409 `delegation_depth_exceeded`.
     - Load the history: the task's delegations with `delegated_at` and `accepted_at`.
     - If `is_delegation_loop(..., chain=chain)` → open a SEPARATE `tenant_session` (the pattern in `api.count_run_task`), because a raise rolls back the caller's transaction. In it:
       - `tasks.add_review_item("delegation_loop", target=TargetRef(type="task", id=task), project_id=..., payload=..., dedupe_key=f"delegation_loop:{task_id}", session=own)`.
       - `api.cancel_run(ctx, run, reason="delegation_loop", session=own)` for each active run of `caller.profile_id` (the master's own runs; keys have no run_id). Filter with `api.dispatched(...)` or `workflow_id IS NULL`.
       - Then raise 409 `delegation_loop`.
     - Otherwise insert the delegations row (`id = uuid7()`, `parent_run_id = caller.run_id`, `tainted`).
     - If there is a note, add it with `tasks.add_comment(s, call.actor, task_id, note)`. A key comment is tainted and rendered as an untrusted block.
     - Call `api.request_run(task_id, RunKind.TASK, delegation_id=id, ctx=..., session=call.session, now=call.now)`. This already gives 409 `agents_paused` while paused, so pauses are respected.
   - **Chain walk:** from the task, loop with a seen set and at most about 10 steps:
     - Get `created_by` through a NEW additive `tasks.api` read (TaskOut has no `created_by`), e.g. `task_creator(s, task_id) -> tuple[str, UUID|None]` returning (created_by, parent_id). This is a small cross-module edit; list it.
     - If it is `task_token:<id>`: `auth.task_token_runs(s, [id])` (includes revoked tokens) → run → `runs.delegation_id` → that delegations row → append a `DelegationRecord` and continue from its `child_task_id`.
     - Else, if the task has a parent_id, continue from the parent.
     - Else stop.
     - Subtasks are depth one (`_check_parent` gives parent_not_root), so in T-07, T3 is a ROOT task made by R2's token. The walk goes through the creator, not the parent.
   - `delegation_snapshot(s, delegation_id) -> WaitOut`: read the runs row (status, state_seq, workflow_id, task_id). Fill:
     - `question`: the pending question prompt, or the approval description / action_class, when waiting.
     - `result_summary`: `tasks.result_of_run(...).summary` when succeeded.
     - `task_status`: `tasks.get_task(...).status`.
     - 404 for an unknown delegation.
   - `wait_for_task` loop (plan pseudo-code). In the api process, use `deadletter.dbos_client().get_event_async(workflow_id or str(id), f"state:{seq+1}", timeout_seconds=slice)`. The DBOS 3.1.0 client polls every 1 s and returns None on timeout; this was checked in Context7 `/dbos-inc/dbos-docs` (client.md get_event_async).
     - Use a SHORT slice (1 s) while the run is queued or held: `prepare_run` and `_hold_if_paused` bump `state_seq` without `set_event`. Otherwise use a 30 s slice.
     - Re-read the row after each slice.
     - Fallback if the client misbehaves from the MCP thread: a 0.5 s row poll, as `human.long_poll_decision` does.
     - The wait must NOT hold a transaction. The op is `write=False` with `after_commit`. `invoke` hands the handler's object as-is to `after_commit(caller, answer)` with no input, so return a `WaitOut` subclass carrying the delegation id and timeout in pydantic PrivateAttr. Don't widen core.
4. **`mcp.py`**: register `DELEGATE_TASK` (scope delegate, write, `updates_existing=False`, `project_resolver=_project_of_task`, `master_only=True`, REST `POST /v1/delegations`) and `WAIT_FOR_TASK` (scope delegate, read, a resolver from delegation to project, `master_only=True`, REST `GET /v1/delegations/{delegation_id}/wait`, `after_commit`). Update the module docstring table.
5. **`router.py`**:
   - `POST /v1/delegations`: `RoutePolicy(auth="session_or_key", scopes={"delegate"}, idempotent=True)`, no project_param (a body task id can't be looked up), `surface.rest_twin`.
   - `GET /v1/delegations/{delegation_id}/wait`: no session dep, query `timeout_seconds: Annotated[int, Query(ge=1, le=600)] = 600` and `schema_version`, `project_param="lookup:delegations"` (needs `register_project_lookup("delegations", fn)`), `surface.rest_twin_detached`.
6. **`review_kinds.py`**: register `delegation_loop` (owner agents, actions `("accept", "snooze")`, impact_scope "task"). Payload: `task_id`, `delegations: int`, `cycle: bool`, `stopped_runs: list[UUID]`.
7. **`events.py::_apply_result_decision`**: on accept, stamp `delegations.accepted_at` (envelope.occurred_at) for the result's run delegation, `item.payload["run_id"]`, inside the same session.
8. **Shared-file edits**:
   - `tumnis/core/agent_surface.py`: remove `delegate_task` and `wait_for_task` from `PENDING_TOOLS`. Otherwise T-P2-01-16 fails with "never both".
   - `backend/tests/_mcp.py`: add `SAMPLES["delegate_task"]` and `SAMPLES["wait_for_task"]`.
     - The delegate sample makes a fresh `label="ai"` task each call with first_action and acceptance_criteria, and inserts a `ready` project profile for the project if it has none, as `running_run` does.
     - The wait sample inserts, as owner, a terminal (succeeded) run plus a delegations row in the project, with `timeout_seconds=1`, so both doors give equal JSON.
   - `backend/tests/meta/_authz.py`: add `LOOKUP_TARGETS["delegations"]` (`run_row` plus a delegations row with the same id).
   - All three (agent_surface, `_mcp.py`, `_authz.py`) are also touched by open PR #132 (P2-17). Keep the edits small.
9. `make gen` regenerates `schemas/mcp/v1/tools.json`, `schemas/openapi.json` and the frontend client. Never hand-edit those.
10. Open the PR as the prompt file says, and request `@coderabbitai review` once.

## Gotchas

- Run make check with semgrep paths under TMPDIR, since `~/.semgrep` is read-only:
  `SEMGREP_SETTINGS_FILE=/tmp/claude-1002/P2-06-c0/semgrep.yml SEMGREP_LOG_FILE=/tmp/claude-1002/P2-06-c0/semgrep.log SEMGREP_VERSION_CACHE_PATH=/tmp/claude-1002/P2-06-c0/semgrep.ver make check`
- frontend/node_modules is installed in this worktree (`npm ci` done).
- Red unit imports needed `# type: ignore[attr-defined]` for mypy, as P2-09 did. These were removed in 7253beb once the names existed.
- The worktree guard refuses compound commands that contain `$VAR` or grep patterns with `|`. Use literal paths and simple commands.
- T-09's master run is started by `_delegation.start_master_run`. It inserts a running master `plan` run with `workflow_id=supervise:<run>:<version>` and starts `supervise_run`. No public api starts a master `dispatch_run`; check whether P2-12 #134 adds one.
- "Its own runs" for a key caller means the active runs of `caller.profile_id`. State this in the PR.
- Requirement tag "Design decision 6" is free text. The traceability script only validates tags that look like PRD ids.
- DOCKER LOAD RULE: run `make test-int` bare, at most once per continuation. Otherwise rely on CI.

## Scott items (so far)

- `DelegateOut.tainted` was added beyond the plan's interface, because the locked taint sweep requires it (decision 35).
- Delegation code lives in a new `agents/delegation.py` instead of `api.py`, to avoid an import cycle and conflicts in the shared file.

## Verify

```bash
cd backend && uv run pytest -q -m "not integration and not contract" tumnis/modules/agents/tests/unit/test_delegation_rules.py
make test-int   # bare, from the worktree root
```
