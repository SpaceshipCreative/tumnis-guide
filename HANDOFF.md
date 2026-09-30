# P1-07 handoff (c3 -> c4): Quick-add labels and overrides

**PR #93** is open (https://github.com/SpaceshipCreative/tumnis-guide/pull/93). Push with `/usr/bin/git push origin HEAD:wp/P1-07`. From a fresh throwaway worktree: `/usr/bin/git fetch origin`, `/usr/bin/git merge origin/main`, then `/usr/bin/git merge origin/wp/P1-07`.

c3 stopped on the coordinator's "STOP NOW": the VM ran out of RAM. No local processes or containers were left running.

## State at the stop

- Head **990b912** was pushed, but GitHub Actions had not started a `ci` run for it after 8 minutes (only third-party check suites were queued). First check `gh run list --branch wp/P1-07 --workflow ci.yml`.
- If there is still no run, compare the diff of `.github/workflows/ci.yml` (the new serial step) with a working run. An empty commit or re-push may be needed. If Actions refuses the workflow, fix it and push.
- CodeRabbit shows "Review paused" on 990b912, and `@coderabbitai review` was requested. There are 0 unresolved threads so far.

## Commits by c3 (after c2's 7c1f22f)

- `23c18bb` test(decisions): the label event-time test (red).
- `d8efdb9` fix(decisions): the AI label's `task.updated` carries the answered event's `occurred_at`.
  - `as_of` goes through `start_label` → `label_task` → `apply_label` → `set_ai_label`/`set_label_suggestion(now=...)`.
  - Reason: an api on a fixed clock (tests, `POST /v1/test/clock`) made the label look newer than a later rename, and search kept the old title. The coordinator approved this approach; it is cited in the PR body.
- `76e2eca` test(search): `_search.drain` re-lists workflows for the drained events until none run. This was a CodeRabbit finding; replied to and resolved.
- `6a4cc92` and `bc0fb64` merge main: 1e7c296 (includes spec PR #95 for T-P0-20-03, merged) and 00a130d (includes #89, the fake-script hook). `ab7c8d2` regenerates the schemas.
- `7df3629` ci: T-01 runs serially (coordinator decision, option 2).
  - `serial` marker in `backend/pyproject.toml`; T-01's `pytestmark` gets `pytest.mark.serial`.
  - `make test-int` runs `integration and not serial` in parallel, then `integration and serial` with `-n 0`.
  - `ci.yml`'s integration job has a second step, "Serial integration tests (timing budgets, -n 0)", with `if: ${{ !cancelled() }}`.
- `79729b3` test(core): reset deadlock test (red; reproduced the e2e failure locally).
- `990b912` fix(core): `truncate_tables` retries up to 3 times on `DeadlockDetected`.
  - The e2e run on bc0fb64 failed A1.5 with `POST /v1/test/reset` 500. The cause was a deadlock between the reset's TRUNCATE and a label workflow's write followed by its emit.
- This commit: HANDOFF.md.

## CI history of T-01 (budget: p95 < 1 s)

- d8efdb9: passed, then failed on the rerun (p95 1,099.9 ms, fastest 722 ms).
- bc0fb64 (still under xdist): integration green, including T-01 and T-P0-20-03. e2e failed on the reset deadlock, which is now fixed.
- The serial step has not run in CI yet.
- Coordinator plan: observe 3 CI runs with the serial step.
  - If T-01 passes all 3: send "#93 MERGE-READY at <sha>".
  - If it misses: stop and report. The coordinator takes it to Scott as option (4), the budget.
- Option (3), trimming decide's roughly 150 ms of overhead, is optional and was not started.
- Don't restore the xfail, and don't build a dedicated loop or process.
- Local numbers (-n 0): p95 850 to 950 ms.
- Pooled engines in the `dbos` fixture brought no gain and are not committed (SQLAlchemy documents the cross-loop hazard).

## Next steps

1. Get CI running on 990b912 or later. Check that the serial step runs T-01, and that e2e is green (the reset retry).
2. Watch 3 CI runs. Use `gh run rerun <id> --failed` to rerun only the integration job. Pushing cancels in-progress runs (`cancel-in-progress`).
3. Check CodeRabbit for new comments on 7df3629..990b912; fix or decline them, and resolve the threads.
4. If #80 merges first: re-chain `tasks_0006` after `tasks_0005`, and wire `apply_review_decision(..., label_override=False)`.
5. When CI is green and CodeRabbit is clean: send "#93 MERGE-READY at <sha>" to main. Delete HANDOFF.md in a chore commit.

## Running single Docker tests locally

- Temporarily set the `test-int` recipe (Makefile lines 40 and 41) to one `-n 0` run of your path, redirecting output to `/tmp/claude-1002/P1-07-c4/out.txt`.
- Run bare `make test-int`, then `/usr/bin/git checkout -- Makefile`. Never commit the tweak.
- `make check` needs `SEMGREP_SETTINGS_FILE`, `SEMGREP_LOG_FILE` and `SEMGREP_VERSION_CACHE_PATH` set to paths under $TMPDIR.
- The `frontend/node_modules` directory is installed in this worktree.

## PR body

The body is up to date at 990b912: the event-time fix, the serial step, the reset retry, shared-file edits (`backend/pyproject.toml`, `Makefile`, `.github/workflows/ci.yml`), the pooling result and the SQLAlchemy docs citation. Add the serial CI results once they are in.

## Deviations (in the PR body)

1. A1.1 `test.fail()` kept (coordinator: until P1-08 lands, even with #89 merged).
2. A1.4 step 3 xfail kept (no "Acme site" in the seed).
3. No decisions queue: the plan's fallback.
4. No "Just added" list.
5. The seed has no `triage.reserved_judgments`.
6. `set_ai_label` and `set_label_suggestion` take a session, `for_title`, and `now` (the answered event's time).
7. `decisions.api.use_providers` test override.
8. T-01 runs serially in CI and in `make test-int` (coordinator decision).

## Scott items

- T-01's budget, only if it misses in the serial step (the coordinator's option 4).
