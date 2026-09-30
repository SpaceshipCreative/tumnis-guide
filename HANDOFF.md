# HANDOFF: P1-06 Project provisioning (continuation 2)

PR **#78** https://github.com/SpaceshipCreative/tumnis-guide/pull/78 (branch `wp/P1-06`; push with
`/usr/bin/git push origin HEAD:wp/P1-06`). Handoff requested by the context watcher.

## Commits (on wp/P1-06)

| SHA | What |
| --- | --- |
| 1dd1f18 | `test(agents): P1-06 spec tests (red)` |
| 9c3eea5 | `feat(agents): P1-06 profile_name_for and provision_outcome` |
| 5c7c4bc | `feat(projects): P1-06 AgentProfileChoice on project.created` (WIP of the first agent) |
| 96a70c5 | `feat(projects): P1-06 agent profile choice on POST /v1/projects and project.created` |
| f34bbd7 | `feat(agents): P1-06 provision_profile workflow, subscribers, retry and master registry`; integration markers T-01..07 removed (not run locally, see CI) |
| 0384c9b | `feat(daemon): P1-06 provision handler` (T-09 green, marker off) |
| e904ad7 | `feat(frontend): P1-06 create or link the project's agent` (T-10 green, marker off) |
| d73bb94 | `chore: P1-06 remove the handoff` |
| 2e48c1d | `fix(agents): P1-06 a ready profile ends a late provision untouched` |
| 4986c3e | Merge origin/main (P1-15, P2-14): worker.py conflict kept both sides; `make gen` rerun |
| (next) | `fix: P1-06 review` (CodeRabbit round 1, see below; committed with this handoff) |

## Spec tests

All markers are off. T-08, T-09 and T-10 are green locally. T-01..07 (Docker) are verified only by CI:
locally Docker answers 500 on container start under the VM load. The CI run for 4986c3e
(run 36669712458) was in progress at handoff. **Check it first**:
`gh pr checks 78`, then `gh run view <id> --log-failed`.

## CodeRabbit round 1 (review on d73bb94): 2 inline threads, both FIXED in the handoff commit, NOT yet replied or resolved

1. `daemon/tumnis_daemon/provision.py` (thread comment id 4140839616): the env copy failed after install, so the
   daemon now runs `hermes profile delete <n> --yes` before answering `failed`. The stub `hermes`
   supports `profile delete`. **TODO**: add the unit test (the draft was refused by the worktree
   guard as a heredoc; write it with the Write tool) in `daemon/tests/unit/test_provision.py`.
   `test_a_profile_without_its_env_is_removed`: make `profile_env_file` a directory
   (`read_bytes` raises IsADirectoryError), `handle_provision(create)`, and assert `failed/hermes_error`
   and that `profiles_dir/beta-app` does not exist.
2. `frontend/src/components/project/ProjectPage.tsx` (id 4140839627): profiles past the first 100.
   `GET /v1/agents/profiles` now takes an optional `project_id` filter (`api.list_profiles(...,
   project_id=)`, router). `settings/queries.ts` adds `projectProfileQuery(projectId)`
   (`limit: 1`), and ProjectPage uses it. `make gen` has been run. **TODO**: run the
   vitest project files (`--maxWorkers=2`), typecheck and `make check`. Consider an
   integration test for the filter in `agents/tests/integration/test_profiles_api.py`
   (a new test, not an edit of a locked assertion).
Then reply in each thread (what changed and the SHA) and resolve it with the GraphQL
`resolveReviewThread`. Note: CodeRabbit said "You've used all 10 included reviews". A
re-review may not come. Comment `@coderabbitai review` once after pushing anyway.

## Remaining steps

1. Finish the two TODOs above, then `make check` (the script is in
   `$TMPDIR/p106c1/check.sh` of the last agent. Or: `make check` with the SEMGREP_* env
   under /tmp/claude-1002/; vitest times out under load, so rerun failing files with
   `--maxWorkers=1`).
2. Push, reply to and resolve the 2 threads, then comment `@coderabbitai review`.
3. Watch CI (`gh pr checks 78`). The likely CI risks are:
   - integration T-P1-06-01..07 (never run);
   - other integration tests that create projects with DBOS running now also start
     `provision_profile` (no runner: `no_runner` plus a `provisioning_failed` review item).
     A test that counts review items or workflows may need a look.
   - T-07's retry must answer within the 1 s timeout under CI load.
4. Update the PR body's test table (the body file was `$TMPDIR/p106c1/pr-body.md`. Edit it with
   `gh pr edit 78 --body-file`). Add the deviation: `GET /v1/agents/profiles?project_id=`
   is new (review fix).
5. Delete HANDOFF.md in a chore commit when done.

## Decisions and deviations (all in the PR body)

- `provision_profile(workspace_id, project_id, mode, link_name, attempt)`: adds the tenant and the attempt.
- `--yes` instead of the plan's `-y` (Hermes v2026.6.5 docs).
- There is no `daemon/packaging/`. The README documents `template_dir`.
- The form was extracted to `CreateProjectDialog.tsx`, and the status is the agent line in `ProjectHeader`.
- `AgentProfileChoice` has no model validator (it broke the polyfactory bodies of the tenant and
  authz sweeps). `create_project` answers 422 `invalid_profile_choice` for a link without a name,
  and create ignores `name`.
- api cannot import workflows (acyclic contract), so `api.register_provision_starter` is the seam.
- The enqueue from a subscriber runs in a fresh `contextvars.Context()`, because DBOS forbids
  starting a workflow inside a step.
- T-06 cannot check Jev labels (P1-07 is not merged). T-05's planning request is built by hand (P1-11).
- `RESERVED_PROFILE_NAMES` is unchanged. The master name is avoided via `MASTER_PROFILE_NAME`.
- An unlinkable link name gets a generated name and `invalid_name`.
- The `send_and_wait` refactor was skipped.
- Fake stacks without a runner: `no_runner` plus a review item per new project.
- Migration `agents_0002` (`0002_profile_provisioning.py`) COLLIDES with P2-07's `agents_0002`
  (wp/P2-07, no PR yet). The later merge renumbers.

## Scott items

- Manual check on the Hermes VM: creating a project creates its profile (`hermes profile list`).
- Install the template at `/opt/tumnis-daemon/share/profiles/project-template` and
  `/etc/tumnis/profile.env` (mode 0600, owner `tumnis-agent`).
- Confirm `hermes profile install <dir> --name <n> --yes` does not prompt, and that
  `hermes profile show <n>` exits non-zero for a missing profile (undocumented).
- Preview checks: kill test 20 runs in a row, the `provisioning_failed` retry, and the dialog at phone width.

## Context7 docs cited

- DBOS 3.1.0 (`/dbos-inc/dbos-docs` and the installed source).
- Hermes v2026.6.5 (`/nousresearch/hermes-agent/v2026.6.5`: `reference/profile-commands.md` and
  `user-guide/profile-distributions.md`; `profile delete <n> --yes` is shown there too).

## Verify commands

- `cd backend && uv run pytest -q -m "not integration and not contract" tumnis/modules/agents tumnis/modules/projects`
- `cd daemon && uv run pytest -q tests`
- `cd frontend && npx vitest run --maxWorkers=2 src/components/project`
- From the root, bare: `make test`, `make test-int` (Docker was failing locally; rely on CI).
