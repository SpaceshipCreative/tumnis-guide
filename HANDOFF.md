# HANDOFF: P2-04 (dispatch_run and the run view), continuation c3 next

Branch `wp/P2-04` (pushed to `origin/wp/P2-04`). Based on main at cf20116 (#106); main had
not moved at c2. **No PR is open yet.** No CodeRabbit review, no CI run on a PR yet.
Scratch folder for the next agent: `$TMPDIR/P2-04-c3/`.

## Commits

| Commit | What |
| --- | --- |
| 38f6010 | c0: backend spec tests (red) + `_runs.py` helpers |
| b6939c8 | c1: rules, migrations, api, payloads, workflows, frontend red specs (WIP) |
| e3e7e34 | c2: subscribers, result decision, ws result path, sweep signal, routes, `post_result`, fixtures + `make gen`, `run` live entity |
| (this commit) | c2: authz/MCP sweep helpers for runs and `post_result`, RunOut trimmed, RunView/ResultItem/useRunEvents + queue wiring, T-02/T-14/T-15/T-16 markers removed, handoff |

`make check` passes at this commit (run through `$TMPDIR/P2-04-c2/check.sh`, which sets the
three SEMGREP_* variables under $TMPDIR; copy it to your own folder).

## State of the spec tests

- GREEN, markers removed: T-03, T-17, T-19 (unit, c1); T-02 `test_other_project_not_blocked`
  and T-14 `test_double_run_click_refused` (XPASS(strict) in c2's local `make test-int`);
  T-15 RunView and T-16 ResultItem (Vitest; `test.fails` -> `test`, prettier reflowed the
  RunView body's indentation only; watch spec-guard on the PR).
- STILL xfail (strict), reasons unknown (xfailed tests print no traceback): T-01, T-04,
  T-18, T-08, T-05/06/07 (kill), T-09, T-10, T-11, T-12, T-13. The coordinator now forbids
  more than one full local `make test-int` per continuation (Docker overload): use CI as the
  authority. To see their tracebacks, open the PR and read CI, or temporarily remove markers
  in a throwaway commit on the PR branch and read CI's integration log, then put back the
  markers of the ones still red (never weaken an assertion).
- c2's local `make test-int` (before the fixes in this commit) also failed these, now fixed:
  `tests/meta/test_authz_matrix.py` (no LOOKUP_TARGETS entry for runs: added `run_row` in
  tests/meta/_authz.py), `tests/meta/test_mcp_*` (no `post_result` sample: added
  `running_run` + `_post_result` in tests/_mcp.py; the sample posts for the newest task
  token's run of the project, so the scope matrix's task-token caller posts for its own run).
  Environment-only failures seen locally (not ours): folder_sync s3 (Docker 500),
  pgbouncer (Docker 500), typeahead latency (load), rclone (known), decisions
  test_log_never_holds_input_text (CancelledError under load).

## Remaining steps (in order)

1. Push (done by this commit), open PR1: `gh pr create --base main --head wp/P2-04 --title
   "[P2-04] impl: dispatch_run and the run view" --body-file <file>`; then
   `gh pr comment <url> --body "@coderabbitai review"` once. Decide: this branch now also
   holds the frontend (RunView, ResultItem, useRunEvents, queue wiring). Recommended: keep it
   in PR1 and leave PR2 (`wp/P2-04-impl-2`) for A2.1 support: the drawer Run button and
   `run` search param on `projects.$projectId`, files touched in the run view, the
   compose-stack fake runner hooks `POST /v1/test/fakes/runner/script` `{task_title, runs}`
   and `GET /v1/test/fakes/runner/last-packet` (coordinator: they are P2-04's, R-37; see
   `frontend/e2e/phase2.ts` on `origin/wp/P2-00-spec` for the script shape: stream,
   upload_artifact, result, ask_human steps), `signals.py` refactor, `supervise_run`,
   `reconcile_runs` (hourly on maintenance), settings wiring for `configure_runs`.
2. Read CI; fix the still-red P2-04 integration tests one at a time; remove each marker
   only after it passed. Likely suspects to check first:
   - T-01: `workflow_status(waiting) == "ENQUEUED"` with the in-test DBOS; the runs queue
     now polls every 0.5 s (`RUNS_QUEUE_POLL_S`).
   - T-09/10/11/12: REST `POST /v1/runs/{id}/result` with the packet's task token (the
     `run_world` key must give `tasks:write`); `accept_result` then `run.signal{result}`.
     T-09 posts twice: if the run ends (token revoked) before the second post, it 401s
     (race; report to Scott, never weaken).
   - T-13: `ws._insert_event` now stores stream text through `api.log_text`; paging by seq.
   - T-04/T-18: `configure_runs` in the test process; `_supervise` caps; closing line.
   - T-08: sweep emits `run.signal{runner_lost}` for dispatch_run runs (workflow_id =
     run id), in `sweep_workspace_step`.
   - T-05..07 kill tests: worker subprocess; ws `_result` (test process) goes through
     `accept_result` and the worker's relay delivers the signal.
3. Review loop (pr-review-loop.md), MERGE-READY to main with SendMessage.

## Decisions settled in c2 (binding; c0 1-15 and c1's list still hold)

- `accept_result`: a caller holding a task token posts only for its run (403
  `run_mismatch`); a key with NO run may post for any run it can reach. Needed by the
  locked P2-01 sweeps (T-P2-01-05/10/11 require every write op to succeed for an
  ALL_SCOPES key with no run). PR-body deviation from the plan's "task token must belong
  to inp.run_id".
- ws `_result`: a run whose `workflow_id == str(run_id)` (dispatch_run) takes
  `_dispatched_run_result` (succeeded -> `accept_result` in a savepoint, ProblemError
  logged; else or invalid output -> `run.signal{agent_failed}`); never DBOS. A `cancelled`
  answer after the run ended is acked and dropped (the log stays closed).
- DBOS 3.1.0 `DBOS.send_async` from inside a step sends directly (no step recorded) with
  the idempotency key (checked in dbos/_core.py `send_bulk`); `deliver_signal` needs no
  fresh-context wrapper. `register_queue(..., polling_interval_sec=)` is the poll kwarg.
- Part A alignment: `result.posted` carries summary and links; `run.started` status
  `running`; `run.finished` `duration_s`.
- `RunOut` trimmed to what the run view reads (error and active_seconds_used default), so
  the locked RunView fixture validates against the generated zod.
- RunView elapsed counts in 5-second steps (the locked T-15 checks 1:10 then 1:15 under
  `shouldAdvanceTime`, where real time drifts the fake clock by about a second).
- ResultItem: standalone = feedback field + Reject disabled until typed (T-16). In the
  review queue `confirmReject`: Reject opens the field, "Confirm reject" sends (A2.1 on
  wp/P2-00-spec clicks Reject first, then fills Feedback, then Confirm reject). Enter and r
  stop propagation so the queue does not act twice.
- `POST /v1/tasks/{task_id}/run` is `session_or_key` + `tasks:write` + idempotent +
  `lookup:tasks`; run routes use `lookup:runs`.

## PR-body deviations to record (c1's list plus these)

- No deduplication_id on the partitioned `runs` queue (DBOS 3.1.0 refuses it; Context7
  /dbos-inc/dbos-docs); deterministic workflow id + partial unique index.
- No `priority_enabled` in DBOS 3.1.0 `register_queue`; priority via `SetEnqueueOptions`.
- `over_ceiling` takes the ceiling as a parameter; `dispatch_run(workspace_id, run_id)`;
  P2-09 pause/held not built.
- `result` review item added in `agents.accept_result`; `results.task_id` ON DELETE CASCADE;
  `run_events.seq` one global sequence; `result/task_result/1` schema not registered.
- Keyless `post_result` allowed (above); ResultItem `confirmReject` mode; 5 s elapsed step.
- `run_skill` partitions `runs` by profile id, `dispatch_run` by project id: a project using
  both can reach 4 at once.
- `send_to_agent` replayed after `inside_send` issues a second token; run end revokes both.
- Migrations: `agents_0005` (after agents_0004), `tasks_0007` (after tasks_0006). P1-08 also
  adds a tasks_0007: whichever merges second re-chains to tasks_0008 (coordinator).
- Docs cited: Context7 /dbos-inc/dbos-docs (queue tutorial: partitioned queues, priority;
  client.send idempotency_key), dbos 3.1.0 source (`_dbos.py register_queue`,
  `_core.py send_bulk`).

## Scott / coordinator items

- SPEC CONFLICT to raise: T-P2-04-16 (ResultItem: Reject disabled until feedback typed)
  vs A2.1 on wp/P2-00-spec (click Reject, then fill Feedback, then "Confirm reject").
  Resolved by the queue-only `confirmReject` mode; flag it anyway.
- A2.1 (`frontend/e2e/journeys/J3.spec.ts`) is P2-00's; after it merges, merge main and
  un-fail A2.1 only if it passes (needs PR2's pieces above).
- R-29's 24-hour ceiling remains a plan default flagged for Scott.
- Open: who mints a profile's API key (keyless profiles dispatch without a token).
- T-09 second-post race vs token revocation (report, never weaken).

## Verify commands

```bash
bash $TMPDIR/P2-04-c3/check.sh      # a copy of c2's check.sh: make check with SEMGREP_* set
cd backend && uv run ruff check . && uv run mypy tumnis && uv run lint-imports
cd frontend && npx vitest run src/components/runs src/components/review src/lib
make test-int                       # bare, at most once per continuation; CI is the judge
```

Push with `/usr/bin/git push origin HEAD:wp/P2-04`. Use `/usr/bin/git`. Never merge.
