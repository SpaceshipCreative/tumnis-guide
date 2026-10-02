# HANDOFF: FIX-followups-2 (continuation c0 to c1)

PR: #151 (DRAFT), https://github.com/SpaceshipCreative/tumnis-guide/pull/151
Branch: fix/followups-2. Push with `/usr/bin/git push origin HEAD:fix/followups-2`.
Prompt: ~/tumnis-coordinator/prompts/wave1/FIX-followups-2.txt (binding, incl. the OVERRIDE).
Scratch: /tmp/claude-1002/FIX-followups-2-c0/ (CI logs, check.sh, fw/ Hypothesis reproductions). Use your own c1 folder.

## Status per item

1. **Planning plan writes on one connection: DONE.**
   - Commits: dc6f27e6 (red tests), 6d19e4f4 (fix), 2c1f840a (markers removed).
   - Test: backend/tumnis/modules/planning/tests/integration/test_plan_writes_one_connection.py.
     - 8 parametrized writes count app-engine connects with cold caches: accept_item, accept_all, remove_item, swap_item, split, move, schedule_block, record_app_open.
     - Plus the swap against a waiting ACCESS EXCLUSIVE request on working_hours.
   - Red proof: all 9 were XFAIL(strict) in run 36947282155 (integration-a). They PASS in run 36947965892 (integration-a green).
   - Fix in planning/api.py:
     - New `_timezone(ctx, session)`: `auth.workspace_timezone` in the caller's transaction, else the settings cache as before.
     - `day_calendar`, `_compute_day` and `_ahead` take `session=`. `get_plan`, `swap_item`, `_day_free` and `_zone`/`record_app_open` pass it.
     - `schedule_block` computes the day calendar inside its own transaction.
     - Read paths without a session are unchanged.
   - Behaviour note for the PR body: in-request timezone reads are now uncached (read in the transaction, fresher).
2. **Harness git env: DONE.**
   - Commits: aeb9bab7 (red, raises=AssertionError, confirmed XFAIL locally), 2d21949b (fix), 74a3a0f4 (marker off).
   - Test: profiles/harness/tests/test_skill_run_git_env.py.
   - Fix: `_git` builds env without GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE.
   - Docs: git-scm.com/docs/git, "Environment Variables" (GIT_DIR turns off discovery; GIT_INDEX_FILE overrides `$GIT_DIR/index`).
3. **Relay wake latency: MEASURED, NO RELAY CHANGE YET.** Report to main next (see "Next steps").
   - Diagnostic probe commits: 49c649b1 and c67dff18. Removed in 578cbc95.
   - CI run 36947965892 (15 passes) and run 36948678842 (200 passes), on GitHub runners:
     - NOTIFY wakes the relay at once: relay_once starts ~0 ms after the emit commit returns.
     - relay_once (claim + modules.enabled + 2 enqueues + mark sent) takes 15-60 ms.
     - The DBOS events queue dequeue waits up to ~0.21 s (EVENTS_QUEUE_POLL_S 0.2 ± 5% jitter).
     - The handlers take ~10-20 ms.
     - emit to both deliveries: median 0.098 s, p90 0.12, p99 0.277, max 0.351 s over 200 passes.
     - Relay startup: LISTEN plus first drain in 16-42 ms, well inside the test's 0.5 s sleep.
     - **A gen2 GC pause of 0.448 s** was caught (run10: the emit itself took 0.474 s). The process holds the test, relay and DBOS threads, so a full collection stops all of them.
     - The flake logs (run 36939199367, job 110626488070: 1.06 s; older run 36681472988: 1.07 s) show no DBOS "Contention detected" warnings in the call phase.
   - Conclusion: the relay is ~10x inside the 1 s limit. The rare >1 s is a whole-process stall (GC, plus CPU starvation on a 4-vCPU runner running 4 xdist workers and the service containers), not wake latency.
   - Options for main:
     - (a) No product change. Accept the rare flake, or have Scott decide on the locked limit.
     - (b) Test-infra, not the locked test: in the shared `dbos` fixture, `gc.collect(); gc.freeze()` before yield, so a gen2 collection mid-test scans only the test's own objects. #112 already freezes after collection.
     - (c) Product: lower EVENTS_QUEUE_POLL_S 0.2 to 0.1. This cuts the median by ~0.05 s and the poll wait in half, at the cost of doubling idle dequeue queries. It does not address a 1 s stall.
     - (d) Product: `gc.freeze()` at worker start-up (tumnis/worker.py) after imports and launch. This helps production GC pauses, but not the test, which doesn't run the worker main.
   - The prompt says: "If no safe product fix exists, stop and report the measurements and options to main." The relay code is unchanged.
4. **Decision 69 (Hypothesis FlakyFailure in T-P2-08-01): DONE, pending CI.**
   - Root cause (run 36944342967, job 110642844715): the strategies draw no `random`. DBOS 3.1.0's background threads call the global `random` (dbos/_queue.py poll jitter and partition shuffle, _scheduler.py jitter, _sys_db.py retry backoff). Hypothesis 6.168.3's `control.deprecate_random_in_strategy` sees the global state change during a draw and warns, and the error filter turns that into a FlakyFailure.
   - First fix 22a803e7: a filterwarnings mark on the locked test. spec-guard flagged it (edited_pytestmark). Reverted in 409fdd46 at the coordinator's request.
   - Current fix 0e79a909: `_private_dbos_random()` in backend/tests/fixtures/__init__.py, called in the `dbos` fixture before launch. It rebinds those DBOS modules' `random` to a private `random.Random()`.
     - Local reproduction (fw/test_fw.py, fw/test_fw2.py): a thread drawing the global random during Hypothesis draws fails without the fix and passes with it.
     - The locked test file is now identical to main, so spec-guard should go green with no label.
   - Shared-file edit: backend/tests/fixtures/__init__.py (the dbos fixture plus the helper). List it in the PR body.

## CI state

Last full run (36948678842, head c67dff18):
- integration-a: green.
- integration-b: failed only on the probe (now removed).
- spec-guard: failed only on the taint pytestmark (now reverted).
- Everything else: green.
- preview: pending (expected).

HEAD 578cbc95 + this handoff are not yet CI-verified.

## Next steps (exact)

1. `/usr/bin/git fetch origin && /usr/bin/git merge origin/main` (run as separate commands), then `bash` a check script like /tmp/claude-1002/FIX-followups-2-c0/check.sh. It sets SEMGREP_* to scratch; `make check` needs that because ~/.semgrep is read-only. Then push.
2. SendMessage main with the relay measurements and options (item 3), and wait for its choice. If it picks (b), (c) or (d), do it test-first in its own commit. Never touch tumnis/core/tests/integration/test_relay.py.
3. Wait for CI with ONE background wait. Confirm spec-guard and integration-a/b are green, and that T-P2-08-01 passes.
4. Rewrite the PR body (gh pr edit 151 --body-file <file>). Include:
   - summary;
   - per-layer results;
   - shared-file edits: tests/fixtures/__init__.py;
   - deviations: decision 69 is fixed in the fixture rather than the test; the relay has no product change, or whatever main chooses;
   - the in-request timezone read note;
   - docs cited: git-scm env vars; Hypothesis 6.168.3 source control.py plus the changelog 6.135.24 note on the global-random deprecation warning; DBOS 3.1.0 source _queue.py. Context7 was unreachable (its PreToolUse hook timed out), so the first-party sources and the installed pinned source were used. Say so.
   - End with a blank line and `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
5. `gh pr ready 151`, then `gh pr comment 151 --body "@coderabbitai review"` once. Run the review loop (~/tumnis-coordinator/pr-review-loop.md).
6. When CI is green and CodeRabbit has no open threads, SendMessage main: "#151 MERGE-READY at <sha>". Do not merge.

## Verify commands

- Profiles: `cd profiles && uv run pytest -q harness/tests/test_skill_run_git_env.py`
- Lint/unit: `bash /tmp/claude-1002/FIX-followups-2-c0/check.sh`
- Integration: CI only (DOCKER LOAD RULE). Planning tests run in integration-a; T-P2-08-01 and the relay tests run in integration-b.

## Gotchas

- The worktree guard refuses compound commands with variables, `cd x && git`, or complex quoting. Run git commands one per call. Put multi-step shell in script files under scratch.
- Job logs: `timeout 120 gh api --allow-escape-sequences repos/SpaceshipCreative/tumnis-guide/actions/jobs/<id>/logs` with allowed_domains `*.blob.core.windows.net`.
- Scott items: none new. Relay option (a) would need Scott if the locked limit is ever to change.
