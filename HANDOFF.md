# HANDOFF: P2-02 (Task tokens and the packet builder), c2 -> c3

c2 stopped on the context watcher's "HANDOFF NOW". Implementation is complete; the PR is
open and in the review loop.

- **PR:** #104 https://github.com/SpaceshipCreative/tumnis-guide/pull/104 (branch `wp/P2-02`,
  title "[P2-02] impl: Task tokens and the packet builder"). Body file (kept up to date):
  `/tmp/claude-1002/P2-02-c2/pr-body.md` (copy to your own scratch folder `P2-02-c3/`).
- `@coderabbitai review` was requested ONCE right after opening (quota is tight: don't
  request again unless no review arrives after ~10 minutes of polling).

## Setup for c3

Throwaway branch at main: `/usr/bin/git fetch origin`, `/usr/bin/git merge origin/main`,
`/usr/bin/git merge origin/wp/P2-02`, push only with `/usr/bin/git push origin HEAD:wp/P2-02`
(needs the Bash `allowed_domains: ["github.com"]`; gh needs `api.github.com`). Then
`cd backend && uv sync --frozen --all-extras` and `npm ci --prefix frontend`.

## Commits (c2)

- `2fcedcf` feat(agents): golden task packets and the task_run_request schema (T-01..03 unmarked)
- `9809125` feat(auth): workspace-scoped task tokens and token_expired on revoke (auth_0006)
- `b4fcf54` feat(agents): every dispatch carries the run's task token; get_task_packet (agents_0004)
- `18d0e63` test(agents): unmark T-10, 11, 13, 14, 15; tests for decisions 30 and 31
- `c08fa2c` chore: remove the unmark helper
- `66da50f` test(auth): unmark T-12
- `d09c89f` merge origin/main (9b35155; one conflict in agents/api.py `__all__`, both kept)
- `81de7ee` chore: remove P2-02 handoff notes (c1's; this file is c2's new one)
- `3dee1aa` fix(auth): squawk-ignore the NOT NULL drop; gitleaks allow-list for the golden token
- plus this handoff commit.

All 15 spec tests (27 cases) are unmarked; every marker came off after XPASS(strict) in a
local run. No `spec:P2-02` markers remain.

## CI state (run on 81de7ee, before 3dee1aa)

Passed: daemon, lint, red-proof, skills, spec-guard, traceability, unit, version-skew, e2e,
performance, integration, GitGuardian. Failed, both fixed in `3dee1aa` (verify on the next run):
- `contract`: all 498 tests passed; the squawk step rejected auth_0006 (`ban-drop-not-null`).
  Fixed with a `-- squawk-ignore ban-drop-not-null` comment inside the `op.execute` (local
  `squawk_migrations.py` now passes both revisions).
- `security`: gitleaks found 8 "leaks" = the made-up token in the 8 golden files. Fixed with
  an allow-list in `.gitleaks.toml` for exactly `^tmt_abcdefghijkl_A{43}$` (local gitleaks
  `dir` over the golden folder: no leaks).
- `preview` stays pending (no homelab runner): expected.
- If `contract` shows cancelled (time budget), `gh run rerun <run-id> --failed` once.

## Remaining steps

1. Push `3dee1aa` + this commit (done by c2 if the push succeeded; check `git log origin/wp/P2-02`).
2. Update the PR body (`gh pr edit 104 --body-file <file>`) with two more shared-file edits:
   `.gitleaks.toml` (golden token allow-list) and the squawk-ignore in auth_0006 (reason in the
   migration comment: auth's own readers treat NULL as "no project"; the previous release reads
   `{NULL}` as a limit no project is in and never issues a project-less token). Cite squawk's
   README "Ignore rules via SQL comments" (https://github.com/sbdchd/squawk, Context7 `/sbdchd/squawk`, squawk-cli 2.66.0).
3. Watch CI (`gh pr checks 104`) and CodeRabbit (`gh api repos/SpaceshipCreative/tumnis-guide/pulls/104/reviews`,
   `.../pulls/104/comments --paginate`, GraphQL reviewThreads). Handle every comment per
   `~/tumnis-coordinator/pr-review-loop.md`; never edit a spec-test assertion or a golden.
   A polling script is at `/tmp/claude-1002/P2-02-c2/watch.sh` (run it with Monitor).
4. When CI is green and CodeRabbit has no open threads: SendMessage to "main":
   `#104 MERGE-READY at <sha>`.
5. Delete this HANDOFF.md in a chore commit before MERGE-READY (unless handing off again).

## Decisions and deviations (all in the PR body)

- Scott decision 30 built: `task_tokens.project_id` nullable (auth_0006); `keys._row` drops the
  NULL so a master run's token has `project_ids == frozenset()`; `issue_run_token` refuses a
  missing project except for plan/notify (`WORKSPACE_RUN_KINDS`). Master profile run -> project
  None; project profile -> its project. Test `test_master_run_token_reaches_no_project`.
- Scott decision 31 built: `agents.api.run_ended` revokes and writes `[redacted]` over
  `runner_messages.payload.packet.callback.task_token` (message id uuid5(run, "run")) in one
  transaction. Test `test_run_end_erases_the_token_from_the_stored_message`.
- Keyless profiles dispatch without a token (P1-04 locked tests use keyless profiles; auto-minting
  would answer the open Scott question). A keyed profile never dispatches without one: a missing
  or revoked key refuses the dispatch (`no task token: ...`).
- Token backstop: `expires_at = greatest(:now, now()) + 24 h` (DB wall clock, R-29), because
  T-12's key probe judges with the real clock while the test issues with the fixed clock.
- A revoked `tmt_` answers 401 detail `token_expired`; revoked `tmn_` unchanged.
- `dispatch_step` issues the token inside the step and sets `packet.callback` (creating one for
  enrich/plan packets); `finish_step` always calls `run_ended`; a refused dispatch ends it too.
- `get_task_packet` op in `agents/mcp.py` + REST twin `GET /v1/tasks/{task_id}/packet`
  (`kind` query) on the agents router; caller facts `agents.profile`; removed from
  `PENDING_TOOLS`; `SAMPLES["get_task_packet"]` in `tests/_mcp.py`.
- `frontend/src/lib/live-map.ts`: `agentsGetTaskPacket` in the task entity's details (T-P0-22-11).
- No REST exposure of `api_key_id` on profiles (only `set_profile_key`), pending the Scott item.
- c1's deviations (seam for P1-17/P1-08/P1-11, CR escaping, policy vocabulary, relative callback
  URLs, optional policy/callback, comment trust) are in the PR body.

## Migrations

- `agents_0004` (down `agents_0003`): `agent_profiles.api_key_id`. P2-03 also plans
  `agents_0004`; whichever merges second re-chains to `agents_0005`.
- `auth_0006` (down `auth_0005`): `task_tokens.project_id` nullable.

## Scott items

- Who creates a profile's API key? (`set_profile_key` links an existing key; nothing mints one.)

## Verify commands

- make check (sandboxed): `SEMGREP_SETTINGS_FILE=/tmp/claude-1002/P2-02-c3/semgrep-settings.yml SEMGREP_LOG_FILE=/tmp/claude-1002/P2-02-c3/semgrep.log SEMGREP_VERSION_CACHE_PATH=/tmp/claude-1002/P2-02-c3/semgrep-version make check`
- Docker layers: bare `make test` (unit + contract: 1931 passed locally) and `make test-int`
  (all P2-02 tests pass; local timing failures on a loaded VM are listed in the PR body).
- squawk: `cd backend && uv run python ../scripts/ci/squawk_migrations.py tumnis/modules/auth/migrations/0006_workspace_task_tokens.py`
- Heredocs and `$TMPDIR` in commands are refused by the worktree guard: write scripts with the
  Write tool and use literal `/tmp/claude-1002/...` paths.
