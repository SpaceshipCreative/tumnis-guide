# HANDOFF: fix/ci-integration-budget

Task: bring the CI `integration` job (budget `timeout-minutes: 10`, locked by
T-P0-03-16 in `backend/tests/ci/test_ci_config.py`; do NOT change it) comfortably under
budget, aiming for pytest under about 7 min on the GitHub runner. Never weaken a test
assertion. Open the PR with `~/tumnis-coordinator/open-pr.sh fix/ci-integration-budget
"fix(ci): integration layer back under its 10-minute budget" <body-file>`, then run the
review loop (`~/tumnis-coordinator/pr-review-loop.md`). Don't merge.

Stopped early: the VM is being rebooted. No code changed yet; no containers started; no
CI run of this branch yet; no PR yet.

## State

- Branch `fix/ci-integration-budget` = `origin/main` (f256fa1) plus this handoff commit.
  `git branch -m` succeeded, but writing `.git/config` failed (read-only in the sandbox),
  so there is no upstream: always push explicitly with
  `/usr/bin/git push origin fix/ci-integration-budget`.
- Baseline evidence (from the coordinator): run 36629696440 (PR #53, head dbe079b), job
  `integration`: `723 passed, 7 xfailed in 581.67s (0:09:41)`, cancelled at 10:00.
  Progress 69%->78% took 2:20.
- When I looked (about 21:22Z), run 36629696440 was on a second attempt still running
  (integration job id 109624903279), so its first-attempt log is under
  `gh api repos/SpaceshipCreative/tumnis-guide/actions/runs/36629696440/attempts/1/jobs`
  (REST JSON: the job key is `id`, not `databaseId`). Downloading a job log
  (`gh api repos/SpaceshipCreative/tumnis-guide/actions/jobs/<id>/logs`) needs
  `allowed_domains: ["*.blob.core.windows.net"]` on the Bash call; it returns 404 while
  the job is still running.
- Tooling gotcha: the worktree-isolation guard refuses `gh ... --jq '<complex>'` and
  `python3 -c` with shell variables. Write small scripts into the scratchpad and run
  them with literal paths.

## What I learned from the code

`.github/workflows/ci.yml`, job `integration`:
`uv sync` then `uv run pytest -m "integration and not contract" -n auto` in `backend/`.
Default xdist `--dist load`.

`backend/tests/fixtures/__init__.py`:
- `pg_container` is session-scoped per xdist worker (one Postgres container per worker;
  fsync/synchronous_commit/full_page_writes off).
- `db_template` builds `tumnis_template_<worker>` once per worker (migrated), and `db`
  clones it per test (`CREATE DATABASE ... TEMPLATE`) and drops it after: already the
  template approach, so re-migration per test is not the lever.
- `dbos_sys_db` is one per worker; the `dbos` fixture destroys, reconfigures, resets
  (truncate), launches DBOS and waits for queue-worker threads each test, then
  `_stop_queue_workers` (joins threads with up to 10 s timeout each, and waits for active
  workflows) at teardown. Worth measuring: per-test DBOS launch/teardown cost.
- `worker_killer` creates a fresh DBOS system database per test and starts worker
  subprocesses (`python -m tumnis.testing.run_worker`); `restart_and_drain` polls every
  0.1 s up to 30 s.
- Session services `minio`, `sftp_server`, `clamd` come from `backend/tests/_services.py`
  (not read yet): check whether they are session-scoped and only started when requested.
- `pgbouncer` is session-scoped (per worker).

## Next steps

1. Add `--durations=40` (keep it permanently) and `--dist worksteal` to the integration
   pytest line only (PR #52 also edits ci.yml: keep the edit minimal and inside the
   integration job). Commit (`fix(ci): ...` / `ci: ...`), push, read timings from the
   job log. If the run times out before durations print, also consider a `--junitxml`
   report, or split measurement locally with `make test-int` (bare command, from the
   worktree root; the VM is loaded, so trust CI more; use `-n 2` for any other local
   pytest).
2. Look at the slowest tests: sleeps/polls with long timeouts (shorten only through
   injected clocks/timeouts the test already exposes), per-test DBOS launch/teardown,
   heavy containers (clamd, MinIO, SFTP) started for tests that don't need them.
3. Iterate until pytest is about 7 min or less on CI and all CI is green; then write the
   PR body in `$TMPDIR` (before/after timings, slowest tests before/after, changes and
   safety; end with a blank line and the Claude Code line) and run open-pr.sh.
4. Run the review loop. Final report: PR URL, before/after timings, changes, CI state,
   Scott items (say plainly with numbers if it can't get comfortably under budget).
