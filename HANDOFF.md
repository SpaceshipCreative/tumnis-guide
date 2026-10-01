# HANDOFF: P2-04 impl-2 (wp/P2-04-impl-2), continuation c0

The context watcher sent HANDOFF NOW at about 380k tokens, so I stopped at a clean point.
The task prompt is /tmp/claude-1002/coord7/p2-04-impl-2.txt and the binding scope is
~/tumnis-coordinator/agent-reports/P2-04-PR2-plan.md.

## State

- **Branch:** `wp/P2-04-impl-2`, based on main at fee9afa (#111 merged). It is pushed with
  `/usr/bin/git push origin HEAD:wp/P2-04-impl-2`.
- **PR:** not opened yet. No CodeRabbit review and no CI run so far.
- **`make check`:** passes at 884748c. It needs the semgrep environment variables; see
  `/tmp/claude-1002/P2-04-impl-2-c0/check.sh`.

### Commits

| SHA | Commit |
| --- | --- |
| 17345e6 | `test(agents)`: red spec tests (strict xfail) plus type-checking stubs |
| 0a65851 | `test(frontend)`: red Vitest tests (`test.fails`) |
| d4dcea0 | `feat(agents)`: scripted fake runner, last-packet hook, `reconcile_runs`, run caps from settings, `signals.py` split |
| 884748c | `feat(frontend)`: drawer Run, `run` search param, RunView files and clock, ResultItem region |
| (next) | `chore`: this handoff |

### What landed

**Backend**

- `adapters/fake.py`:
  - `parse_runner_script` accepts the phase 1 body `{profile, skill, result, delay_ms}`
    (key `<profile>/<skill>`) and `{task_title, runs}` (key `task:<title>`, stored with
    `played: 0`).
  - It refuses `ask_human` with 422; P2-05 extends it.
  - `on_dispatch(hook)` is called on every FakeAgent dispatch while the store is enabled.
- `adapters/__init__.py` registers the parser as `runner`.
- `agents/fake_play.py` registers through `on_dispatch`, which avoids an import cycle
  inside the agents package (import-linter acyclic). `workflows.py` imports it.
  - It records the last packet through `fake_scripts.record_run_packet`.
  - It plays this run's steps in a background task:
    - each stream line becomes a run event through the new `api.record_run_event`, which
      `ws._insert_event` now uses too;
    - an artifact becomes an `artifact` event, after the `artifact_refusal` checks;
    - the result goes through `api.accept_result` with actor `device:<nil>`.
  - Pacing: `STEP_PAUSE_S` is 0.1 and `RESULT_HOLD_S` is 4.0 (see "A2.1 timing" below).
- `send_to_agent` wraps `adapter.dispatch` in `use_workspace(ctx)`, so the fake knows its
  workspace.
- `core/fake_scripts.py` adds `RUNNER`, `LAST_PACKET`, `record_run_packet` (an atomic
  count), `last_run_packet` and `redact_run_token`.
  - `redact_run_token` is a no-op unless the store is enabled.
  - `api.finish_run_in` and `api.run_ended` call it with `REDACTED`.
- `core/testing_routes.py` adds `GET /v1/test/fakes/runner/last-packet`. It answers
  `{packet, run_messages}`; before any packet it answers 200 with `{packet: null,
  run_messages: 0}`.
- `workflows.reconcile_runs` is a DBOS workflow, scheduled hourly (`0 * * * *`) on the
  maintenance queue through `agents.workflows.schedules()`. `worker.py` is not edited for
  it.
  - It lists the active runs whose `workflow_id` is null or equal to the run id.
  - It calls `DBOS.list_workflows_async(status=CANCELLED|ERROR|MAX_RECOVERY_ATTEMPTS_EXCEEDED)`.
  - `fail_orphan_step` ends each such run through `finish_run_in`: `failed`, or
    `cancelled` for a held run. Stop reasons are `workflow_cancelled` or
    `workflow_error`, with the closing line "Stopped: the run's workflow ended".
- `api.STREAM_EVENT_KIND` moved here from ws (ws aliases it).
- `api.ENDED_WORKFLOW_REASONS` is new.
- `settings.AgentsSettings` gets `run_active_cap_seconds` and
  `run_wall_clock_ceiling_seconds`.
  - They are fakes only: `Settings` raises `SettingsError("run_caps_need_fakes")`
    otherwise.
  - `worker.configure_agents` calls `agents.configure_runs(...)`.
- `agents/signals.py` takes `signal_of` (was `_signal`) and `deliver_signal`, moved
  unchanged. `events.py` calls `signals.deliver_signal`.
  - Per the coordinator, P2-05 owns `supervise_run` and version-aware delivery.
  - Whichever of the two PRs merges second keeps one copy, in `signals.py`.

**Frontend**

- The route's `run` search param is `z.uuid().optional().catch(undefined)`. `onTask`
  clears `run`; `onRun` sets it.
- `ProjectPage` and `TaskDrawer` take `runId` and `onRun`.
- The drawer's `RunButton` shows for `ai` or `hybrid` tasks in backlog, today or
  in_progress.
  - It posts `/tasks/{id}/run` with `{kind: "task"}` through `useWrite` and `apiWrite`,
    then `onRun(run_id)`. An error shows the problem's detail in a `role=alert`.
- With a `runId`, the drawer shows a "Back to the task" button and `RunView`. The dialog
  keeps the task title as its name.
- `RunView` changes:
  - the elapsed time has `data-testid="run-elapsed"`;
  - it counts 1-second steps under 60 s and 5-second steps after, so T-15 is unchanged;
  - a "Files touched" region (a section labelled by an h3) lists the distinct paths from
    `file` events.
- `ResultItem`: the files list is wrapped in a `<section aria-label="Files touched">`.
  T-16's list query still holds.

### Tests

| Test | Status |
| --- | --- |
| Unit: `agents/tests/unit/test_runner_script.py`, `core/tests/unit/test_worker_run_caps.py` | green, markers removed |
| Vitest: `TaskDrawer.run.test.tsx`, `RunView.files.test.tsx`, `ResultItem.region.test.tsx` | green, `test.fails` flipped to `test` |
| Integration: `agents/tests/integration/test_fake_runner_playback.py` (2 tests) | not run, still `xfail(strict)` |
| Integration: `agents/tests/integration/test_reconcile_runs.py` | not run, still `xfail(strict)` |
| Integration: `tests/integration/test_runner_hooks.py` (2 tests) | not run, still `xfail(strict)` |

Remove each integration marker only after CI shows the test XPASS(strict), or a local
`make test-int` passes. Run `make test-int` at most once per continuation, and as a bare
command.

## Remaining steps

1. `make test-int` once, bare, from the worktree root; otherwise push and read CI.
   - If the five integration tests pass, remove their `xfail` lines and commit with
     `test(agents): unmark ...`.
   - Likely fixes if they fail:
     - **Playback test:** `tasks.change_status` in_review -> in_progress as the user may be
       refused. If so, use the reject flow or a fresh task. Don't change assertions; adjust
       the setup only where it's a non-assertion line.
     - **`redact_run_token`:** the `jsonb_set` / ARRAY literal typing.
     - **`record_run_packet`:** the `excluded.script["packet"]` indexing.
     - **reconcile:** `dbos.cancel_workflow_async` on the fixture class; whether
       `reconcile_runs` can be called directly as a DBOS workflow in the test.
2. Check that `make gen` leaves no diff. Test routes are not in `openapi.json` (grep found
   0 `v1/test`), so there should be none.
3. Open the PR:
   `gh pr create --base main --head wp/P2-04-impl-2 --title "[P2-04] impl-2: run view, Run button, signals and reconcile" --body-file /tmp/claude-1002/P2-04-impl-2-c0/pr-body.md`
   - Write the body first. Then comment `@coderabbitai review` once.
4. Run the review loop. Watch e2e on CI:
   - Accepting the phase 1 runner script shape means J1, J2, J6 and J8 (`test.fail()`)
     get past `fakes.runner.script`. They must still fail somewhere later, or
     `test.fail()` flips.
   - A2.1 keeps `test.fail()`: the seed has no Acme site / `acme-site` profile / "Fix
     footer link" yet (a Scott item).
5. When CI is green and no threads are open, SendMessage to `main`: `#<PR> MERGE-READY at <sha>`.

## PR body: points to include

- **Deviations:**
  - `supervise_run` is not built; P2-05 owns it (coordinator).
  - Phase 1 runner scripts are stored but not played. `run_skill` uses `DaemonTransport`
    directly, not `adapter_for`, so no FakeAgent plays them in compose.
  - `ask_human` steps are refused with 422 (P2-05).
  - The playback is not durable.
  - The fake speaks as actor `device:00000000-0000-0000-0000-000000000000`.
  - The elapsed time counts each second for the first minute.
  - The reconcile end status is `cancelled` for held runs.
  - `fake_play` registers through the `on_dispatch` hook, because a lazy import from
    adapters broke the import-linter acyclic contract.
- **Test-only exception to decision 31:** last-packet keeps the live token until the run
  ends; `finish_run_in` and `run_ended` redact it.
- **A2.1 timing risk:** Playwright's 5 s expect window. The result waits 4 s after the
  last step, so the run view shows the lines, the clock ticking and Stop before In
  review. Not verifiable here without the seed.
- **Shared-file edits:**
  - `backend/tumnis/worker.py` (`configure_agents` calls `configure_runs`);
  - `backend/tumnis/settings.py`;
  - `backend/tumnis/core/fake_scripts.py`;
  - `backend/tumnis/core/testing_routes.py`.
  - None of AGENTS.md, pyproject, uv.lock, Makefile, `.importlinter` or package.json.
- **Docs:**
  - Context7 `/dbos-inc/dbos-docs`: `list_workflows` status filter; scheduled workflows.
  - DBOS 3.1.0 source: `list_workflows_async` runs as a step; `WorkflowStatusString`.
  - Context7 `/tanstack/router`: functional search updates.
  - Context7 `/tanstack/query`: `useMutation` `onSuccess` and `onError`.
- **Scott items:**
  - Seed and acceptance harness: Acme site project, `acme-site` profile and API key, "Fix
    footer link" with its tainted email and brief. Who mints a profile key.
  - The R-29 24-hour ceiling is still a plan default.
  - The spec conflict carried from PR1 (T-16 vs A2.1 reject flow).
