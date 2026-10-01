# HANDOFF: P2-12 (Full template and master skills), continuation c2 -> c3

PR: #134 https://github.com/SpaceshipCreative/tumnis-guide/pull/134 (wp/P2-12 -> main). Do NOT merge.
Push with `/usr/bin/git push origin HEAD:wp/P2-12`. Setup per vm-agent-rules: on a throwaway branch at main,
`/usr/bin/git fetch origin`, `/usr/bin/git merge origin/main`, `/usr/bin/git merge origin/wp/P2-12`.
Envs: `cd backend && uv sync --frozen --all-extras`, `cd profiles && uv sync --frozen`,
`cd daemon && uv sync --frozen --all-extras`, `npm ci --prefix frontend`.
Scratch: `$TMPDIR/P2-12-c3/` (c2's `check.sh` is in `$TMPDIR/P2-12-c2/`: make check with `-n 3` and
semgrep state under $TMPDIR; copy it and fix ROOT/S paths). PR body source: `$TMPDIR/P2-12-c2/pr-body.md`.

## State at handoff

- Branch head before this commit: 2168408 (merge of origin/main fb7d771 = #118 P2-09 kill switch).
  `make gen` after the merge: no drift. Local make-check equivalent: CHECK-OK (backend unit 1763,
  daemon 69, profiles 122 passed / 172 skipped, Vitest 114 files / 254 tests).
- CI on 2168408: everything green EXCEPT `integration`: one failure,
  `tumnis/modules/agents/tests/integration/test_profile_version.py::test_profile_version_logged_with_every_run`
  (T-P2-12-10): `TimeoutError: fake runner: condition not met in 10.0 s` in `_wait_for_run`.
  Root cause in the log (run 36861128997): `dispatch_run` -> `prepare_run` (agents/workflows.py ~557) ->
  `tasks.change_status` -> `_transition` raises `_stale(row)` (409 stale_version). `prepare_run` reads the
  task with `tasks.get_task` (no lock), then `change_status` locks the row and finds the version moved by a
  concurrent writer (the `blocking_impact` decision applied right after `task.created`), so the workflow
  errors and the run never reaches the runner. T-10 passed in CI on e5802ad's follow-up run and on
  36f7a89; checked: #118 did NOT add a task writer near run request (its events.py/workflows.py changes
  are pause/hold only), so this is a pre-existing P2-04 race that T-10's 3 sequential runs hit more often.
- CodeRabbit: all 5 threads resolved (T-10 marker; delete_files ordering fixed in 4ee3b7d; cron project id
  = Scott item 8; run_id in production packets = Scott item 6 / coordinator asking Scott about a separate
  spec-change PR for the goldens, leave it OUT of #134; scratch-file deletions: CodeRabbit withdrew).
- MERGE-READY was sent once for 36f7a89; the coordinator then asked for the #118 merge + regen + check +
  re-send MERGE-READY once CI is green. Not re-sent yet (integration red on 2168408).

## Next steps (in order)

1. `gh run rerun 36861128997 --repo SpaceshipCreative/tumnis-guide --failed` ONCE (or just let CI on the
   handoff commit run). Note: this handoff commit triggers a fresh CI run anyway.
2. Message main about the race as a P2-04 follow-up (do NOT patch agents/workflows.py in #134: P2-09-impl-2
   and P2-17 are active there): mechanism (`get_task` unlocked -> `change_status` stale -> dispatch_run
   errors -> run stuck queued; a user editing a task at dispatch time silently loses the run); proposed fix
   (re-read the task under the row lock before `change_status`, or catch stale once and retry inside
   `prepare_run`). Add it to the PR body's Scott items (`gh pr edit 134 --body-file ...`).
3. If integration fails again on T-10 with the same stale race: harden T-10 (this WP's own unmerged spec
   test; adding a wait is allowed, never change an assertion, as decision 44 did for T-P2-04-11): in the
   loop, after `world.ai_task(...)`, wait until the task's version is stable (e.g. `wait_until` on two equal
   reads of `tasks.get_task(...).version`, or until the blocking_impact decision row exists) before
   `world.request`. List it in the PR body under deviations.
4. When CI is green: delete HANDOFF.md (`chore: remove the P2-12 handoff notes`), push, wait for CI green
   again, then SendMessage to main: "#134 MERGE-READY at <sha>".

## Commits (this WP)

c0/c1: d7c2633 spec tests (red); 1620385 RunOut.profile_version + run view; d524ef2 daemon TUMNIS_TOKEN;
acd8c24 WIP profiles/skills/harness. c2: b36cbc0 case packets + hostile index; 9435d51 T-06/T-08 markers
off; fede6e9 base packets only for the repo's own skill dir (T-P2-11-04 build-around) + CLI refactor;
0fe39dc skill cases (T-11 marker off); 6941c0e harness unit tests; e5802ad lazy MCP SDK import (backend
venv traceability collection); 6ed3ad0 T-10 marker off; 4ee3b7d deletions recorded before the next Tumnis
call (harness/workdir.py); 36f7a89 removed old handoff; 2168408 merge main (#118).

## Decisions and deviations (all in the PR body)

1. Locked T-P2-11-04 hard-codes "orchestrate" as its hypothetical new skill: build-around (SkillRef.path;
   a base packet covers only the repo's own skill directory). Coordinator: fine, list it as a deviation.
2. MCP servers in config.yaml `mcp_servers`; mcp.json kept (locked T-P1-05-12).
3. cron/jobs.json, not cron/project-digest.yaml. 4. TYPESAFE_API_KEY (not JEV_API_KEY).
5. proxmox_destructive -> proxmox_delete_guest. 6. Cases cannot set runs (harness.toml fixes 3).
7. No result.json: post_result input less run_id/idempotency_key; digest_run.v1.json for digests.
8. Pending tools (wait_for_task with `delegation_id`, delegate_task, draft_reply) served as stubs.
9. Case packets carry the whole body in `<packet>`. 10. Deletions recorded at the next Tumnis call and at
   the end (attempt-start snapshot only; scratch files the run creates and removes do not count).
11. One PR.

## Scott items

1. TUMNIS_TOKEN in the profile .env overrides the daemon's per-run token (Hermes .env override=True).
2. Cron jobs from a distribution install paused. 3. Coolify MCP not pinnable (instance version).
4. Pin a Hermes version for the agent host. 5. Real coding task on the Hermes VM and skill cases 3 of 3
   need the homelab. 6. run_id missing from production prompts (golden spec change; coordinator asking Scott).
7. Distribution updates keep an installed profile's config.yaml. 8. Cron digest needs the project id from
   P1-06 provisioning. 9. (to add) P2-04 prepare_run stale-version race (see State).

## Verify commands

```bash
cd profiles && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q
cd profiles && uv run python -m harness check && uv run python -m harness coverage \
  && uv run python -m harness coverage --suite hostile && uv run python -m harness index --suite hostile --check \
  && uv run python -m harness packets --check
bash $TMPDIR/P2-12-c3/check.sh   # make check with -n 3
gh pr checks 134 --repo SpaceshipCreative/tumnis-guide
```
