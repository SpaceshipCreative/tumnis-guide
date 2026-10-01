# HANDOFF: P2-06 Delegation (continuation c1 → c2)

c1 stopped because the context watcher sent "HANDOFF NOW". The branch is `wp/P2-06`; push with `/usr/bin/git push origin HEAD:wp/P2-06`. Use `$TMPDIR/P2-06-c2/` for scratch files.

## PR

- **#139**, "[P2-06] impl: delegation". It is a DRAFT. The body file is `/tmp/claude-1002/P2-06-c1/pr-body.md`; copy it to your own folder.
- CodeRabbit has NOT been asked to review yet; it skipped the PR because it is a draft. When the integration job is green:
  1. update the PR body (fill the Tests section; see "Body updates" below);
  2. run `gh pr ready 139`;
  3. run `gh pr comment 139 --body "@coderabbitai review"` once;
  4. then follow `~/tumnis-coordinator/pr-review-loop.md`.
- When CI is green and CodeRabbit has no open threads, send "#139 MERGE-READY at <sha>" to main with SendMessage.

## Commits on top of main

| SHA | Commit |
| --- | --- |
| `bb2a09a` | test(agents): P2-06 spec tests (red) |
| `7253beb` | feat(agents): delegation rules (T-06, 08, 10 green) |
| `8cb8483` | chore: P2-06 handoff (c0) |
| `e81dd89` | merge `wp/P2-06` into a fresh throwaway branch |
| `1fa098a` | feat(agents): delegate_task and wait_for_task |
| `f94ad68` | test(agents): sweep samples and lookup target for the delegation ops |
| `2b83139` | Merge origin/main (#132 P2-17); generated files regenerated with `make gen` |
| `cb5275e` | fix(agents): wait_for_task answers 404 before master_only; T-01 to 05 and 07 unmarked |
| this commit | chore: P2-06 handoff (c1) |

## What is built

- **Migration and model.** `agents/migrations/0009_delegations.py` (`agents_0009`, after `agents_0008`) and the `Delegation` model in `models.py`.
- **`api.request_run(..., delegation_id=)`**: the run id is the delegation id, and so is the workflow id. Also new: `api.DELEGATION_LOOP` and its line in `CLOSING_LINES`.
- **New `agents/delegation.py`**:
  - `delegate_task` (handler);
  - `delegation_chain`, the chain walk through `tasks.task_creator`, then `auth.task_token_runs`, then `runs.delegation_id`;
  - the loop refusal: a separate `tenant_session` cancels the caller profile's active runs and adds the `delegation_loop` review item;
  - `mark_accepted`;
  - `delegation_project`;
  - `delegation_snapshot` and `wait_snapshot` (handler);
  - `wait_for_task` (`after_commit`): DBOS `get_event_async` slices, falling back to a 0.5 s row poll.
- **`mcp.py`**: `DELEGATE_TASK` and `WAIT_FOR_TASK`, both master-only on scope `delegate`.
- **`router.py`**: `POST /v1/delegations` (201, `rest_twin`) and `GET /v1/delegations/{delegation_id}/wait` (`rest_twin_detached`, no `project_param`).
- **`review_kinds.py`**: `delegation_loop`. **`events.py`**: on accept, stamps `accepted_at` through `delegation.mark_accepted`.
- **Shared-file edits**:
  - `core/agent_surface.py`: the two tools leave `PENDING_TOOLS`; `_project` now locates the row for master-only reads too, so the answer is 404 before `master_only` (A0.3).
  - `tasks/api.py`: `task_creator`.
  - `tests/_mcp.py`: the two SAMPLES.
  - `core/tests/integration/row_factory.py`: `("delegations", "depth"): 1`.
  - `frontend/src/lib/live-map.ts`: `agentsWaitForTask` added to `NOT_LIVE`.

## CI state (run 36869070538, on `2b83139`)

- Everything was green except `integration`, which had 9 failures:
  - XPASS(strict) on T-P2-06-01, 02, 03, 04, 05 and 07. These are fixed in `cb5275e`: markers removed.
  - `test_authz_matrix.py::test_project_limited_key_outside_project_is_404[GET .../wait]` (the "inside" request got 403 `master_only`). Fixed in `cb5275e` by dropping the route's `project_param`.
  - `test_a0_3_tenant_isolation.py[key|session-GET .../wait]` answered 403 where 404 was expected. Fixed in `cb5275e` by the core `_project` change.
- `cb5275e` and this commit are pushed. CI has NOT run on them yet. Check `gh pr checks 139`.

## Still red (strict xfail), with no traceback in the CI log

- **T-P2-06-09** `test_loop_stops_run_with_review_item`
- **A2.4** `tests/acceptance/test_a2_4_delegation_wait.py`

Next step: find WHERE they fail.

1. Add a temporary integration test file of your own: `agents/tests/integration/test_delegation_diag.py`, unmarked, not a spec test. Mirror each flow step by step, with a descriptive assertion after every step, and print the run row, `stop_reason`, review items and the tool outcomes.
2. Push it, read `gh run view <id> --log-failed`, then delete the file.
3. Do NOT remove or loosen the markers to diagnose (Scott decision 51). Locally, run `make test-int` (bare) at most once per continuation.

Suspects for T-09:

- The master run (hand-inserted `plan` run, `supervise_run`) never ends after `cancel_run(..., reason="delegation_loop")`. Check:
  - that `signals.deliver_signal` reaches the `supervise:` workflow;
  - that `stop_agent` (fake runner adapter for the master profile) does not raise.
- `_callers_active_runs` finds nothing, which would mean `caller.profile_id` is None. `arrange_world(master=True)` links the key with `set_profile_key`, so `profile_id` should be set.
- `tasks.add_review_item` raised inside the own session.
- If the cause is outside P2-06's code (workflows, fake runner), it is a Scott item; don't patch locked tests.

Suspects for A2.4:

- Step 3: `review_items(session_client, "question")` returns more or fewer than one item.
- `child_answered` never sees `answered`.
- The final wait is not `done`/`succeeded`. Compare with T-03 and T-04, which pass.

When each one XPASSes in CI, remove its marker (Scott's standing approval) and push.

## Body updates for the PR

- Fill `<!-- TESTS -->` with the per-layer results. `make check` is green locally (backend unit, frontend Vitest, the perf meta tests). Add contract, integration and e2e from CI.
- Under deviations, add: the wait twin has no `project_param` (T-P0-14-11 needs a limited key inside its project to pass authorization, and a master-only op never lets a non-master key through); the op's resolver still gives 404 outside the project.
- Under shared-file edits, add the core `_project` change for master-only reads.
- The body still mentions `LOOKUP_TARGETS["delegations"]` and `lookup:delegations`. Both were removed in `cb5275e`; delete those mentions.

## Deviations and Scott items (keep in the PR body)

1. The code lives in the new `agents/delegation.py`, not in `api.py` (import cycle, and `api.py` is a hot shared file).
2. `DelegateOut.tainted` is added for the locked taint sweep (decision 35).
3. Extra `delegations` columns: `project_id`, `parent_run_id`, `note`, `accepted_at` and `tainted`.
4. "Its own runs" for a key caller means the active dispatched or supervised (or not yet dispatched) runs of the profile the key is linked to, plus the token's run.
5. `agent_not_provisioned` covers a missing profile and the `provisioning` and `not_provisioned` statuses.
6. The wait twin has no `project_param`; the core `_project` locates the row for master-only reads.

## Docs cited

- DBOS 3.1.0 `DBOSClient.get_event_async` (Context7 `/dbos-inc/dbos-docs`, client.md). Its implementation was checked in `.venv` `dbos/_sys_db.py`: async, `event.wait_async` plus `asyncio.to_thread`.
- FastAPI query parameter models (`/websites/fastapi_tiangolo`).
- Pydantic `PrivateAttr` (`/pydantic/pydantic`).

## Gotchas

- Run make check like this:
  `SEMGREP_SETTINGS_FILE=$TMPDIR/P2-06-c2/semgrep.yml SEMGREP_LOG_FILE=$TMPDIR/P2-06-c2/semgrep.log SEMGREP_VERSION_CACHE_PATH=$TMPDIR/P2-06-c2/semgrep.ver make check`
- Fresh worktree: run `cd backend && uv sync --frozen --all-extras` and `npm ci --prefix frontend`. `make gen` needs `node_modules`.
- The worktree guard refuses compound commands that mix git with python heredocs or `cd ..`. Write scripts with the Write tool under `$TMPDIR`, run them, then call `/usr/bin/git` in its own command.
- CI runs only on the PR, not on pushes; a PR with merge conflicts gets NO run. After main moves, merge `origin/main`, take main's side for generated files, then run `make gen`.
