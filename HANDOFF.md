# HANDOFF: P2-04 (dispatch_run and the run view), continuation c4 next

**PR #111** https://github.com/SpaceshipCreative/tumnis-guide/pull/111, branch `wp/P2-04`, open.
It includes main up to 9fb5496 (#108: P2-00 acceptance, with A2.1 J3.spec.ts and
phase2.ts/testHooks.ts). CodeRabbit reviewed once, when the PR opened; don't request a new
full review unless it's needed (quota).
Scratch folder for the next agent: `$TMPDIR/P2-04-c4/`. Copy these from
`/tmp/claude-1002/P2-04-c3/`: `check.sh` (make check with the SEMGREP_* vars; fix the
paths), `unmark.py` (removes one named test's `xfail(strict)` marker) and `crshow.sh`.

## Commits in c3 (after c2's e01b695)

| SHA | What |
| --- | --- |
| 69c1398 | merge origin/main (#108) |
| bdb67bf | `_runs.workflow_status` reads DBOS on a plain thread (the sync API refused a running loop: the T-01 bug) |
| 3c726bc | semgrep `tumnis-sql-fstring`: no f-string in the agents_0005 partial index (security job) |
| 10bc95b | `_runs.settle()`: `world.request` and `wait_until` wait until the test relay has claimed every committed outbox row. The relay runs on the test loop, and FakeRunner's sync `wait_for` blocks that loop, so run.requested was never relayed. This was the root cause of most red tests. |
| b0d0730 | `finish_run_in`: waiting_on_human, then running, then succeeded, for a result posted while waiting (CodeRabbit critical). New test `test_run_edges.py::test_result_while_waiting_on_human_succeeds` |
| 842234a | `_apply_result_decision`: rerun in a savepoint. A refusal other than run_already_active is logged, and the rejection stands (CodeRabbit major). Test `test_reject_kept_when_the_rerun_is_refused` |
| ccdd715 | `ws._insert_event` locks the run row before its event takes a seq, so events commit in seq order (CodeRabbit major) |
| a14e5d5 | ResultItem: Escape or Cancel refocuses the card (CodeRabbit minor). New `ResultItem.focus.test.tsx` |
| 12d143b | markers removed: T-04, T-08, T-09, T-10, T-11, T-12, T-13, T-18. All XPASS(strict) in CI run 36780630695 |
| 0b6c07f | `worker.main` calls `install_peppers(settings)`. The worker issues task tokens, and only the CLI boot checks installed the peppers. Likely why the kill tests never reached their killpoints (60 s timeouts) |
| 3bdb084 | `settle()` also waits until the world's fake runner holds a `run` message for every running run of its profiles. prepare_run marks a run running before its packet reaches the runner, which raced T-01's final `len(runs()) == 3` |
| 19f4e89 | useRunEvents keeps the events and cursor in the query cache (CodeRabbit nitpick) |
| (this) | handoff |

## Spec-test state

- GREEN, markers removed: T-02, T-03, T-04, T-08, T-09, T-10, T-11, T-12, T-13, T-14,
  T-15, T-16, T-17, T-18, T-19.
- STILL `xfail(strict)`: T-01 (`test_two_runs_per_project_third_waits`) and T-05, T-06,
  T-07 (`test_dispatch_kill.py`, 3 params). CI run 36782373006 (commit 3bdb084) showed 4 F
  in the progress dots, but it was CANCELLED at 10:16 (98%) by the job's 10-minute
  timeout, before the summary printed. The 4 F may be these 4 as XPASS(strict), but that
  isn't proven. Remove their markers only after a completed CI run lists them as
  `FAILED ... [XPASS(strict)]` (use unmark.py). I asked for one rerun of that run's failed
  job, but this handoff push cancels it. Read the CI run for the handoff commit instead.
- DENIED (don't route around it): removing markers in a diagnostic commit to see tracebacks
  ([Security Test Removal]), and reading Makefile/pytest config for that purpose. The
  coordinator knows. The only allowed feedback is CI XPASS(strict) plus reading the code.

## CI state

- The integration job is over its locked 10-min budget. Main's pytest takes 7:06; ours
  took 9:14 in run 2 (kill tests at 60 s each) and was cancelled in run 3. The coordinator
  has been told. Scott decision 8 allows a 15-min budget only through a spec-change PR the
  coordinator labels. NEVER edit ci.yml or test_ci_config.py here.
- Lever inside the helpers: about 10 s of teardown in each test that leaves a dispatch_run
  parked in recv (DBOS destroy joins background threads with a 10 s timeout). Idea: at
  `relay()` exit, cancel the active dispatch_run workflows with
  `DBOS.cancel_workflow_async(str(run_id))` (DBOS only, no DB rows change). First check in
  dbos/_sys_db.py that cancel wakes a parked recv. Do NOT emit run.signal{cancel} there:
  T-13 pages events after relay exit and a "Stopped by you" line breaks it.
- The kill tests should now take about 10 s each, not 60, if the pepper fix was their cause.
- Everything else was green on 3bdb084: lint, unit, contract, security (after 3c726bc),
  e2e, performance, spec-guard, traceability. `preview` stays pending (expected).

## Review threads

All 4 CodeRabbit inline threads were replied to and resolved: seq ordering (ccdd715),
refused rerun (842234a), waiting to succeeded (b0d0730), and focus (a14e5d5). The nitpick
(useRunEvents cache) is fixed in 19f4e89; no thread to resolve. Check for new comments
after the next push.

## Remaining steps

1. Read CI for the handoff commit. If integration completes, remove the markers of every
   P2-04 test listed as XPASS(strict) and push.
2. If the budget still cancels it, try the teardown lever above, then report to main.
3. Update the PR body (`gh pr edit 111 --body-file`). The test section should list the
   markers now off. Add these deviations: seq lock on every run-event writer; waiting to
   running to succeeded in finish_run_in; rerun refusal keeps the rejection; worker
   installs peppers (shared file backend/tumnis/worker.py). Test helpers: settle semantics.
   Then add these Scott items: the pepper gap and the budget.
4. When CI is green and there are no open threads: SendMessage main "#111 MERGE-READY at
   <sha>".
5. PR2 (`wp/P2-04-impl-2`, from wp/P2-04): the A2.1 pieces. These are the drawer Run button
   and the `run` search param on projects.$projectId, files touched in the run view, and
   the compose fake runner hooks `POST /v1/test/fakes/runner/script` `{task_title, runs}`
   and `GET /v1/test/fakes/runner/last-packet` (`{packet, run_messages}`; see
   frontend/e2e/testHooks.ts and phase2.ts on main). Also `supervise_run` and signals.py,
   reconcile_runs, and the configure_runs settings. Un-fail A2.1 only if it passes.

## Decisions and deviations

c0, c1 and c2 hold (see git history of this file: a4ef89b, b6939c8, e01b695), plus the c3
fixes above. For the PR-body deviations list, see e01b695's HANDOFF and the current PR body.

## Scott / coordinator items

- NEW: the worker never installed the API-key peppers. Fixed in shared file
  backend/tumnis/worker.py (0b6c07f).
- NEW: the integration CI budget (above).
- Spec conflict: T-16 standalone Reject vs A2.1's Confirm-reject flow (queue-only
  `confirmReject` mode).
- R-29's 24 h ceiling is a plan default. Open: who mints a profile's API key.
- T-09 double post vs token revocation race (it passed in CI; still note it).
- Denied diagnostic (above).

## Verify commands

```bash
bash $TMPDIR/P2-04-c4/check.sh            # make check
cd backend && uv run ruff check . && uv run mypy tumnis
cd frontend && npx vitest run src/components/runs src/components/review src/lib
gh pr checks 111 --repo SpaceshipCreative/tumnis-guide
gh api --allow-escape-sequences repos/SpaceshipCreative/tumnis-guide/actions/jobs/<job>/logs   # needs allowed_domains *.blob.core.windows.net
```

Push with `/usr/bin/git push origin HEAD:wp/P2-04`. Use `/usr/bin/git`. Never merge.
