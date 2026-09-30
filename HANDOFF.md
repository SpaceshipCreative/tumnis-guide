# HANDOFF: P1-06 Project provisioning (continuation 3)

PR **#78** https://github.com/SpaceshipCreative/tumnis-guide/pull/78 (branch `wp/P1-06`; push with
`/usr/bin/git push origin HEAD:wp/P1-06`). c2 handed off on the context watcher's HANDOFF NOW.

## Commits (on wp/P1-06)

| SHA | What |
| --- | --- |
| 1dd1f18 | `test(agents): P1-06 spec tests (red)` |
| 9c3eea5 .. e904ad7 | c1's implementation (profile_name_for, AgentProfileChoice, workflow, daemon handler, frontend) |
| 2e48c1d | `fix(agents): P1-06 a ready profile ends a late provision untouched` |
| 1e07378 | `fix: P1-06 review` (CodeRabbit round 1: daemon deletes a profile whose .env failed; `GET /v1/agents/profiles?project_id=`) |
| f731715 | `test(agents): P1-06 restore xfail markers on T-01..07` (c1 removed them without them ever running; CI run 36669712458 failed all 7) |
| a9d0938 | `fix(agents): P1-06 start provisions wherever the subscribers load`: events.py imports workflows (the starter was unregistered in the dbos fixture: every delivery dead-lettered), and the deduplication id is dropped (DBOS 3.1.0 refuses it on the partitioned `runs` queue). Also the daemon test `test_a_profile_without_its_env_is_removed` |
| 7185d77 | Merge origin/main (P2-07 #75): conflicts kept both sides; migration renumbered to **`agents_0003`** (down_revision `agents_0002`) |
| ea89f57 | `test(agents): P1-06 remove T-01..07 markers (passed in CI)`: CI run 36678006617, integration job 109767116614 on 7185d77 showed `[XPASS(strict)] spec:P1-06` for all 7, as the only failures (956 passed) |
| 3bc87da | Merge origin/main (P0-25 #65, 79909cc): ProjectPage.tsx import conflict, kept both |
| 5361a37 | `fix(agents): P1-06 review: retry a name collision, route a link to its runner` (CodeRabbit round 2), **NOT PUSHED before this handoff commit; pushed with it** |

## State at handoff

- All T-P1-06-01..10 markers are off. T-01..07 passed in CI (XPASS proof above) and locally (`--runxfail`, and again with no marker at 5361a37: 9 passed with the routing tests).
- CI on 7185d77: everything green except `integration` (the expected XPASS(strict) proof) and `preview` (pending, no runner). CI on 3bc87da and 5361a37 had not been checked at handoff.
- Local on the merged tree: `make check` green apart from vitest load timeouts (all pass with `--maxWorkers=1`; full vitest with `--maxWorkers=2`: 70 files and 133 tests pass). `make test`: 1707 passed. Daemon: 32 passed. `make test-int` at 7185d77: all pass except 7 XPASS (expected then) and local-load timing tests unrelated to P1-06 (relay poll backstop 2.2 s against 1.2 s, typeahead p95, the known rclone timeout, a backup-freshness setup error).
- `alembic heads`: one agents head, `agents_0003`.

## Review threads

- 4140839616 (daemon .env) and 4140839627 (profiles past 100): replied (4141417711, 4141418309) with 1e07378 / a9d0938; both RESOLVED.
- 4140962198 (xfail markers): replied (4141678791) with ea89f57; RESOLVED.
- **4141485335** (thread `PRRT_kwDOUx-vCc6naS-r`, choose_name_step collision retry) and **4141485360** (thread `PRRT_kwDOUx-vCc6naS-9`, route a link to the runner that owns the profile): FIXED in 5361a37, **not yet replied or resolved**.

## Next steps

1. `gh pr checks 78`: confirm CI is green on 5361a37 (preview stays pending). If integration fails on one of the known flakes (#56 e2e reset, relay timing), rerun the failed job once.
2. Reply to 4141485335 and 4141485360 with 5361a37 (`gh api -X POST repos/SpaceshipCreative/tumnis-guide/pulls/78/comments/<id>/replies -F body=@file`), then resolve both (`gh api graphql -f query='mutation{resolveReviewThread(input:{threadId:"<PRRT id>"}){thread{isResolved}}}'`). Check no other thread is unresolved.
3. Update the PR body: a draft is at `/tmp/claude-1002/P1-06-c2/pr-body.md` (copy it into your own `$TMPDIR/P1-06-c3/`). Replace `INTEGRATION_RESULT` with the CI result; state that T-01..07's markers are off after the CI XPASS (ea89f57); add deviation 15 (round 2: `choose_name_step` retries only a name collision; `pick_runner_step(workspace_id, link_name)` prefers the runner whose inventory lists a linked profile) and the new tests (`test_provision_routing.py`, `test_provision_name_retry.py`); cite the DBOS step tutorial (`retries_allowed`, `max_attempts`, `should_retry`; retries are off by default). Then `gh pr edit 78 --body-file <file>` (if GraphQL returns 502, retry later).
4. Delete HANDOFF.md in a `chore: P1-06 remove the handoff` commit, push, and report `#78 MERGE-READY at <sha>` to the coordinator once CI is green and 0 threads are unresolved.
5. If main moves again: `/usr/bin/git fetch origin`, `/usr/bin/git merge origin/main`, keep both sides, `make gen`, check `alembic heads` still shows `agents_0003` as the one agents head, re-verify, push.

## Decisions and deviations (keep in the PR body)

1. `provision_profile(workspace_id, project_id, mode, link_name, attempt)`: adds the tenant and the attempt.
2. `--yes` instead of the plan's `-y` (Hermes v2026.6.5 docs).
3. There is no `daemon/packaging/`; the README documents `template_dir`.
4. The form was extracted to `CreateProjectDialog.tsx`, and the status is the agent line in `ProjectHeader`.
5. `AgentProfileChoice` has no model validator (it broke the polyfactory bodies of the tenant and authz sweeps). A link without a name is a 422 `invalid_profile_choice`.
6. api cannot import workflows (acyclic contract): `api.register_provision_starter` is the seam, and `agents/events.py` imports `workflows` so the starter registers wherever the subscribers load.
7. **No deduplication id** (plan line 9398 names one): DBOS 3.1.0 refuses `deduplication_id` together with `queue_partition_key`, and `runs` is partitioned. The workflow id `provision:<project id>` is the guard (DBOS returns the existing workflow for an id in use). The enqueue runs in a fresh `contextvars.Context()` because DBOS forbids starting a workflow inside a step.
8. T-06 cannot check Jev labels (P1-07 not merged); T-05's planning request is built by hand (P1-11).
9. `RESERVED_PROFILE_NAMES` is unchanged; the master name is avoided through `MASTER_PROFILE_NAME`.
10. An unlinkable link name gets a generated name and `invalid_name`.
11. The `send_and_wait` refactor was skipped.
12. Fake stacks without a runner: `no_runner` plus a review item per new project.
13. `GET /v1/agents/profiles?project_id=` is new (review round 1).
14. Migration `agents_0003`, not `agents_0002` (P2-07 merged first).
15. Review round 2 (5361a37): the name-collision retry and link routing (above).
- Merge with P2-07: daemon `CAPABILITIES` gains `provision`; the `Provision` case of P2-07's `_handle` runs the real handler instead of refusing; the fake runner's default capabilities are `run, provision, health`.

## Scott items

- **Plan vs DBOS docs:** plan line 9398 (P1-06) and **line 9693 (P1-08 enrichment)** specify a deduplication id on the partitioned `runs` queue, which DBOS 3.1.0 refuses. P1-06 uses the workflow id; P1-08 needs a decision.
- Manual check on the Hermes VM: creating a project creates its profile (`hermes profile list`).
- Install the template at `/opt/tumnis-daemon/share/profiles/project-template` and `/etc/tumnis/profile.env` (mode 0600, owner `tumnis-agent`).
- Confirm `hermes profile install <dir> --name <n> --yes` and `hermes profile delete <n> --yes` do not prompt, and that `hermes profile show <n>` exits non-zero for a missing profile (undocumented).
- Preview checks: the kill test 20 runs in a row, the `provisioning_failed` retry, and the dialog at phone width.
- Process note: c2 ran targeted `pytest` with `dangerouslyDisableSandbox` about 8 times to debug and verify single Docker-backed test files (no bare form exists for one file; none was denied).

## Context7 / docs cited

- DBOS 3.1.0 (`/dbos-inc/dbos-docs`): workflow-tutorial "Workflow IDs and Idempotency"; queues reference `SetEnqueueOptions` (`queue_partition_key` cannot be combined with `deduplication_id`); step tutorial (retries off by default; `retries_allowed`, `max_attempts`, `interval_seconds`, `should_retry`); plus the installed source (`dbos/_queue.py` `_validate_enqueue`, `_dbos.py` `step`).
- Hermes v2026.6.5 (`/nousresearch/hermes-agent/v2026.6.5`): `reference/profile-commands.md`, `user-guide/profile-distributions.md`.

## Verify commands

- `cd backend && uv run pytest -q -m "not integration and not contract" tumnis/modules/agents tumnis/modules/projects`
- `cd daemon && uv run pytest -q`
- `cd frontend && npx vitest run --maxWorkers=2`
- From the worktree root, bare: `make test`, `make test-int`, `make check` (SEMGREP_* env under your own `$TMPDIR` subfolder; the script from c2 is `/tmp/claude-1002/P1-06-c2/check.sh`, which hardcodes c2's worktree path, so copy it and edit the path).
