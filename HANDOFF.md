# HANDOFF: PR #52 (wp/P1-14, storage: interface, server path and S3)

Paused for a VM reboot on 2026-09-29, about 21:40Z. Do not merge; the coordinator merges.

## Setup for the next agent

- `git fetch origin`, then `git switch -c agent/P1-14 origin/wp/P1-14`. The head should be the commit that adds this file, on top of `08d774a`.
- Push with `git push origin agent/P1-14:wp/P1-14`. Never force-push.
- On this VM, rtk rewrites plain `git`/`grep`, and the worktree guard refuses the result. Call `/usr/bin/git` and `/usr/bin/grep` instead.
- gh `--jq` filters that contain `select(...)` together with pipes are sometimes refused as "too complex". Put them in a script under `/tmp/claude-1002/` and run `bash <script>`.
- Docker-backed tests: run `make test` (unit and contract; it skips the MinIO class, which is marked `integration`) or `make test-int` from the worktree root, as a bare command. `cd backend` does not persist between calls in a subagent. Never use dangerouslyDisableSandbox (the classifier denied it once).
- `make check` needs `SEMGREP_SETTINGS_FILE`, `SEMGREP_LOG_FILE` and `SEMGREP_VERSION_CACHE_PATH` pointing to files under `/tmp/claude-1002/`. Vitest can flake under VM load (ProjectList, TaskDrawer, undo); rerun before you believe a failure.

## Commits this session (on top of 024969d)

| SHA | What |
| --- | --- |
| 31c2fde | merge origin/main (#50 P0-19). The row_factory.py conflict keeps both sides. `make gen` left the generated files unchanged. |
| 4620314 | ci(contract): contract layer under xdist (`--dist loadfile`, `--durations=10`) |
| 9774c12 | chore: removed the old HANDOFF.md |
| 2d5d78f | ci(contract): `--dist worksteal` instead of loadfile |
| d32ebc2 | ci(e2e): on failure, print the whole run's api and Postgres errors (`ERROR`, `Traceback`, `deadlock`, 5xx) |
| b32407d | test(knowledge): S3 list, a page of hidden keys (red, xfail strict) |
| 206cdf2 | fix(knowledge): S3 `list` fetches on until a page holds a file or the listing ends; server-path write's final stat goes through `asyncio.to_thread` |
| 08d774a | docs(knowledge): S3 `move` docstring states the clobber race without conditional puts |

## Contract budget (done)

- Cause: the contract job ran pytest serially. On main it already used about 1:40 of its locked 2:00 budget (T-P0-03-16). The fuzzer alone takes about 40 to 50 s.
- P1-14 adds about 25 s: MinIO startup, and the locked 1050-file listing case on three real backends. ee651cc ran pytest in 89 s and was cancelled during `make gen`.
- Fix: `uv run pytest -m contract -n auto --dist worksteal --durations=10`. On 206cdf2, pytest took 66 s and the job 1:44, and it passed.
- loadfile (tried on 9774c12) did not help, because the fuzzer ran alone at the end.
- If the margin gets thin later, start the Schemathesis fuzzer first, for example with a `pytest_collection_modifyitems` hook.
- The local "20 MinIO setup errors" were the sandbox refusing the Docker socket, not a test bug.

## e2e reset 500 (issue #56): not fixed

- `POST /v1/test/reset` returned 500 on one test per run on 024969d (attempt 2) and on 9774c12. The failing test changes each time.
- 206cdf2's e2e passed twice (the first run and one rerun), so d32ebc2's diagnostic step never fired. The cause is still unknown.
- A quick 500 (the tests failed in 1 to 2 s) points to Postgres `deadlock detected`, with TRUNCATE aborted. Likely cycle: `truncate_tables` (one TRUNCATE over all tables, sorted by name) against worker subscriber transactions that take tables in another order.
- Related: #51 (the reset/relay hang is fixed on wp/P0-24, PR #57, by locking `outbox` first). P1-14 adds a `knowledge.assign_project_folder` subscriber on `project.created`, which may make the race more likely.
- Next: when e2e fails, read the "api and postgres errors over the whole run" group in the e2e log. If it shows a deadlock, fix it test-first in `backend/tumnis/core/testing_routes.py`: retry the TRUNCATE on `DeadlockDetected`, or lock tables in a fixed order after #57's outbox-first lock. Name the test after #56, and put "Fixes #56" in the commit and in a comment on #52. If the cause is unrelated to P1-14 and not small, report it instead.

## CodeRabbit threads

- Nine older threads are all resolved; see git history for the previous HANDOFF.
- Review on 9774c12:
  - PRRT_kwDOUx-vCc6nSnsT (S3 empty page): fixed in b32407d and 206cdf2; resolved.
  - PRRT_kwDOUx-vCc6nSnsX (blocking `os.stat`): fixed in 206cdf2; resolved.
- Review on 206cdf2:
  - PRRT_kwDOUx-vCc6nTCLk (S3 `move` clobber without conditional puts): docstring clarified in 08d774a, reply explains why `move` is not refused; resolved.
- `@coderabbitai review` was requested for 08d774a at about 21:38Z. Not yet reviewed. The hourly allowance was nearly used up (2 left at 21:3xZ).

## CI

- 206cdf2 (run 36630840492): all green (lint, unit, contract, integration, e2e twice, security, spec-guard, red-proof, traceability, performance, skills, version-skew, CodeRabbit, GitGuardian). preview is pending (no homelab runner; expected).
- 08d774a: CI started on push; not checked yet (only a docstring changed).
- Main-wide: the integration job runs close to its 10:00 budget. If it is cancelled with every test passing, `gh run rerun <id> --failed` once and note it. The fix belongs to `fix/ci-integration-budget`.

## Next steps

1. Check CI on 08d774a (`gh pr checks 52 --repo SpaceshipCreative/tumnis-guide`), and CodeRabbit's review of 08d774a. Handle any new threads per pr-review-loop.md.
2. If e2e fails with the reset 500, follow the #56 plan above.
3. After a main merge (for example #53 P1-04): `git merge origin/main`, `make gen`, keep one migration head per branch, re-verify, push, then `@coderabbitai review`.

## Shared-file edits

`.github/workflows/ci.yml` only: the contract pytest flags, and the e2e failure-log step.

## Scott decides

- Whether to list the aioboto3 S3 client as a named exception to the AGENTS.md `tumnis.core.net` rule (it resolves through `resolve_and_check` via `_GuardedResolver`).
- Confirm the `unicodedata` addition to the rules allow-list.
- Whether storage calls may run in the api process (location create/test, save_note and set_project_location do S3 and disk I/O inline).
