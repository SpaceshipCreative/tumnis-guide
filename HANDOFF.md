# HANDOFF: issue #56 (flaky `POST /v1/test/reset` 500), PR #64

- **PR:** https://github.com/SpaceshipCreative/tumnis-guide/pull/64
- **Branch:** `fix/test-reset-race`. The worktree branch is renamed to it; push with `/usr/bin/git push origin HEAD:fix/test-reset-race`.
- **Label:** `bug`, so red-proof runs.
- **Do not merge.**

## Root causes (both confirmed with tracebacks)

1. **Brief upsert under a generic plan.**
   - `knowledge.api._brief_conflict` bound the partial-index predicate (`WHERE role = $1`). After psycopg's prepare threshold, Postgres tries a generic plan, and the upsert fails with "no unique or exclusion constraint matching the ON CONFLICT specification" in the reset's seed (`seed_document`).
   - Repro: on a stack built from `main`, 6 of 40 back-to-back resets returned 500.
   - Fix: `text("role = 'brief'")`.
2. **Overlapping resets.**
   - A0.6 calls `reset("load")` (2,000 tasks) and times out at 10 s while that reset is still seeding on the server. The next test's reset TRUNCATEs under it, and the two fail each other: `DeadlockDetected` in `tasks.ensure_default_columns`, `truncate_tables` and `outbox.emit`, plus FK and unique violations. Evidence: #63's e2e logs (jobs 109676328193 and 109680520909).
   - Repro: pairs of resets 0.35 s apart gave 15 of 30 responses 500.
   - Fix, step 1 (`e99c4c2`): an app-level `asyncio.Lock`. Locally, 30 of 30 overlapping resets then returned 204. But in CI e2e (run 36652249543), A1.5's reset then waited behind A0.6's load seed and hit Playwright's 10 s request timeout (`apiRequestContext.post: Timeout 10000ms exceeded`).
   - Fix, step 2 (`8a4eb4a`, **not yet verified**): the latest reset wins. Each reset increments `app.state.reset_generation`; `_ResetSink(DatabaseSink)` checks it before every record. A stale seed raises `ResetSupersededError` and returns 409, and the lock keeps the two apart until then.

## Commits (on top of main d92412b)

| SHA | What |
| --- | --- |
| `09bce59` | test(knowledge): brief upserts under a generic plan |
| `a54c42d` | fix(knowledge): literal brief predicate |
| `81a2888` | ci(e2e): upload full api/worker/postgres logs and test-results as `e2e-logs` on failure |
| `4b7132b` | test(knowledge): brief upsert survives the prepare threshold |
| `f6dd448` | merge origin/main (#57, #59, #61) |
| `9433b0a` | test(core): overlapping resets (first version, [204, 204]) |
| `e99c4c2` | fix(core): reset lock |
| `66532e1` | test(core): the test is now `test_issue_56_a_new_reset_supersedes_the_one_seeding` (expects [409, 204]). I changed this new, unmerged test's assertion after my first fix: it's this PR's own test, not a locked spec test. |
| `8a4eb4a` | fix(core): latest reset supersedes |

## State

- **CI at `e99c4c2`:**
  - all green except e2e. e2e failed only on the A1.5 10 s timeout above; the brief and deadlock 500s are gone.
  - red-proof passed.
  - CodeRabbit: pass, no actionable comments, no open threads.
- **`8a4eb4a`:**
  - pushed together with this handoff; CI has not run yet.
  - `make check`: backend green (1011) and lint green. Vitest had 2–3 failures at load average 37, in files this PR doesn't touch; a third run was green.
- **Not done:** the local stack check of the supersede fix. `make up` collided with another agent (`No such container`); the stack was then stopped with `make down`.

## Next steps

1. Watch CI on the new head: `gh pr checks 64 -R SpaceshipCreative/tumnis-guide`. For e2e failures: `gh api --allow-escape-sequences repos/SpaceshipCreative/tumnis-guide/actions/jobs/<job>/logs`, which needs `allowed_domains: *.blob.core.windows.net`. The new `e2e-logs` artifact holds the full logs.
2. Optional local check:
   - `make up`
   - `docker cp` the scratch scripts into `tumnis-test-api-1:/tmp/`: `hammer.py` (N sequential resets), `overlap.py` (N pairs 0.35 s apart) and `abandon.py` (A0.6's pattern: `reset?set=load` abandoned after 2 s, then a reset with a 10 s timeout).
   - Run them with `docker exec tumnis-test-api-1 python /tmp/<script> <args>`, then `make down`.
   - The scripts are in `/tmp/claude-1002/-home-claude-Projects-Tumnis-Guide/9f3b07ec-b982-420c-89db-ecef45097e2c/scratchpad/`.
3. Once e2e passes, rerun e2e twice (`gh run rerun <run> --job <e2e job id>`) to show the flake is gone; report the pass count.
4. Update the PR body. Cause 2's fix is now "the latest reset supersedes" (409 to the stale caller), not just the lock; the stack numbers for overlapping resets need rerunning. Then comment `@coderabbitai review` and handle its threads.
5. The PR title changed from the brief's `fix(core): make /v1/test/reset safe against in-flight worker writes`: the evidence showed neither cause was worker writes. Keep it or align it as the coordinator prefers.

## Notes and items for Scott

- The brief-upsert bug is not test-only. The worker's `knowledge.create_brief` and a user's brief edit through `put_text_document` would fail the same way on a reused connection in production. This PR fixes both.
- The worker's FK-violation noise (deliveries queued before a reset, retried against deleted workspaces) is harmless and left as is.
- The reset returns 409 to a superseded caller; only an abandoned caller ever sees it.
