# HANDOFF: SEED impl-2 (acceptance seed runtime), continuation 5

**PR #138** (https://github.com/SpaceshipCreative/tumnis-guide/pull/138), branch `wp/SEED-impl-2`.
- Push only with `/usr/bin/git push origin HEAD:wp/SEED-impl-2`. Use `/usr/bin/git`, never plain git; no git switch / reset --hard / symbolic-ref.
- Scratch: c5 used `/tmp/claude-1002/SEED-impl-2-c5/`. Make your own `SEED-impl-2-c6/`.
- Helpers (copy, fix the worktree path in the `cd`):
  - `bash /tmp/claude-1002/SEED-impl-2-c5/check.sh`: `make check` with semgrep env vars.
  - `/tmp/claude-1002/SEED-impl-2-c5/probe.py`: A2.2's API path, timed, run INSIDE the api container. `docker compose -f deploy/compose.test.yaml cp <probe> api:/tmp/probe.py`, then `docker compose -f deploy/compose.test.yaml exec -T api python /tmp/probe.py <resets> <wait_s>`. It prints the run's status changes and the last packet's prompt text.
  - `/tmp/claude-1002/SEED-impl-2-c5/watch.sh`: DBOS queue counts each second. Copy it to the `postgres` service and run `docker compose -f deploy/compose.test.yaml exec -T -u postgres postgres bash /tmp/watch.sh 60`.
  - All docker / make up / make e2e / make down commands must be BARE (nothing added). Use literal `/tmp/claude-1002/...` paths, not `$TMPDIR`, in commands near git (the worktree guard refuses variables).
  - `gh run download` needs `allowed_domains: ["productionresultssa16.blob.core.windows.net"]` and `timeout 120`.
- PR body draft: `/tmp/claude-1002/SEED-impl-2-c5/pr-body.md`. It already includes c3's work and T-SEED-29. It is NOT applied yet. Add the CI results and the triage below, then run `gh pr edit 138 --repo SpaceshipCreative/tumnis-guide --body-file <file>`.
- Binding: Scott decision 51 (never touch journey markers to diagnose). Coordinator: agents never remove xfail markers (the classifier blocks it). Scott removes them; report "#138 READY EXCEPT MARKERS at <sha>" with file and line for each.
- The local stack is probably still up (`tumnis-test-*`). Run `make down` first if you don't need it.

## Commits (c5)

| SHA | What |
| --- | --- |
| 168109d | T-SEED-29 (integration, `backend/tests/harness/test_acceptance_runtime.py`, strict xfail, red). A reset cancels the removed world's queued event deliveries, and the new world's deliveries then start without waiting. |
| 451f8c9 | `core/testing_routes.py` `cancel_removed_deliveries()`: after the TRUNCATE and before the seed, it cancels every `deliver_event` workflow still ENQUEUED or PENDING through the api's DBOS client. It is gated on `fake_scripts.enabled() and deadletter.dbos_configured()` (the latter is new in `core/deadletter.py`). |

## ROOT CAUSE 2 (solved): A2.1, A2.2 and T-P4-05-10 never saw their run start

- A reset empties the tables but not the DBOS `events` queue. Old-world deliveries fail on FK errors and back off (full jitter, 5 attempts). Each holds one of the 8 slots while it sleeps (`sleep_async` is a plain asyncio.sleep, and `worker_concurrency` is counted in memory).
- Each acceptance seed queues about 230 deliveries. `run.requested` then waits behind them.
- Probe, before the fix: after 5 back-to-back resets the run stayed `backlog` for 60 s (8 PENDING and about 230 ENQUEUED, draining at about one every 2 s).
- Probe, after the fix: the run reached `waiting_on_human` 1.9 s after the run started, after 5 and after 8 resets. Resets also no longer answer 409 `profile_exists`: an old `provision_project` delivery used to race the seed.
- CI run 36909544176 on 451f8c9: J3 and A2.2 now get far past the run start (details below). Tell the coordinator that #140's T-P4-05-10 should get past this point too.

## CI state

- Run 36909544176 (451f8c9):
  - `integration` cancelled at its 15-min budget. T-SEED-27's XPASS(strict) still fails it until Scott removes that marker.
  - `performance` cancelled.
  - `e2e` failed on ONE unexpected failure: T-P0-24-05 (board keyboard drag, phone; card not in Today). It passed in both local runs, so it looks unrelated or flaky.
  - Every other job is green.
- c5 then ran `gh run rerun 36909544176 --failed` (the one allowed re-run) at about 19:10Z. Read its result first: `gh pr checks 138 --repo SpaceshipCreative/tumnis-guide`. A new push cancels it (concurrency `cancel-in-progress`).
- `preview` pending, as expected.

## Journey triage from CI run 36909544176 (artifact e2e-logs, markers untouched)

- **A2.2:** now passes run start, the review badge, the answer and the task returning to `in_progress` (API). It fails at the last UI step: `taskCards(page, FIX_FOOTER).first()` toContainText "In progress". The Tasks view row (`frontend/src/components/project/TaskRow.tsx`) shows only the title and label chip, with no status, so "Fix footer linkAI".
  - This is a product UI gap (FR-5.7, P2-05 / P0-24), not harness.
  - Options for Scott: (a) TaskRow shows a status chip for AI tasks in progress or waiting, or (b) a spec change makes A2.2 read the row's status group.
- **J3 (A2.1):** now passes dispatch and runMessages 1. It fails at line 63, `untrustedBlocks(prompt_text)` = 2, not 1.
  - The seeded "Fix footer link" is tainted: its email link is tainted (locked SEED tests `test_acceptance_seed_writers.py` line 88 and `test_acceptance_seed.py` line 132 assert this), and P2-08 propagates the taint to the task.
  - `packet_builder.assemble` then wraps the task's own text as untrusted (`trusted = by_user and not tainted`, line 523), alongside the URL context block.
  - This is a conflict between locked tests (J3 vs SEED seed and P2-08), so it goes to Scott and the coordinator. Options: (a) a spec change makes J3 expect 2, or count only non-task blocks; (b) the packet builder keeps a user-written task's own text trusted when its taint comes only from linked items (product, P2-02/P2-08); (c) the seed's email is untainted (breaks the locked SEED tests).
- J1 (P1-11 swap race), J2 A1.1 (chip), J6 (calendar harness, SEED-impl-3), J7/J8 (unscripted master), A1.5: unchanged from the earlier triage in the body draft.

## Remaining steps

1. Read the rerun's CI. If `e2e` fails only on T-P0-24-05 again, look at its error-context in the artifact (`timeout 120 gh run download <run> -n e2e-logs -D <dir>`, with the blob domain allowed). Decide whether the reset cancel affects it (a board move emits `task.updated`; the cancel only runs inside resets). If it is related, fix it test-first. If not, list it as a flake for the coordinator.
2. Look for an XPASS(strict) for T-SEED-29 in `integration` (it may be cancelled at budget again; don't re-run more than once). Do not remove its marker. List it for Scott.
3. Update the PR body draft with: the CI results, the triage above (A2.2 TaskRow status, J3 untrusted-block conflict), and the Scott items below. Apply it with `gh pr edit`.
4. Delete HANDOFF.md in a `chore:` commit (message file: `/tmp/claude-1002/SEED-impl-2-c5/msg-chore.txt`), run check.sh, and push.
5. Check CodeRabbit threads (`gh api repos/SpaceshipCreative/tumnis-guide/pulls/138/comments`; no fresh full review). Fix any actionable ones.
6. Final report: "#138 READY EXCEPT MARKERS at <sha>", listing:
   - T-SEED-27 marker: `backend/tumnis/core/tests/integration/test_reset_lock_timeout.py` line 50.
   - T-SEED-29 marker: `backend/tests/harness/test_acceptance_runtime.py`, on `test_a_reset_cancels_the_event_deliveries_of_the_world_it_removed` (the xfail line is 356).
   - Tell the coordinator the A2.1/A2.2/T-P4-05-10 run path is fixed by 451f8c9, so #140 can retest T-10 once #138 merges.

## Decisions and deviations (cumulative)

- Phase 1 dispatches skip `FakeAgent.dispatch` and its phase 2 hook. The fake's `dispatched` event has `runner_id: None`. `recorded_reply` returns None for JSON null. `adapter_for` is unchanged.
- Seeded tasks start no enrichment (T-SEED-22, `agents/events.py`, a shared-file edit).
- J6 is split to `wp/SEED-impl-3`.
- TICK_WAIT_S is 8 s.
- Coordinator option (c) was replaced by the fake's `runner_lost` (T-SEED-23/24).
- `frontend/playwright.config.ts`: `globalTimeout` 7 min and `maxFailures` 2, CI only.
- c3: `core/testing_routes.py` lock timeout, retry and report (T-SEED-25/27). `AppHeader.tsx` gets a `data-testid` (T-SEED-28). `fake_play._ask` NotFound (T-SEED-26).
- c5: `core/testing_routes.py` `cancel_removed_deliveries` and `core/deadletter.py` `dbos_configured` (T-SEED-29). Only `deliver_event` is cancelled, not every workflow; 1f51f60's sweep of every workflow had stalled the stack.
- c5 tried cancelling before the TRUNCATE as well, to stop the local-only deadlock 500s after the load set (A0.6/P0-29, which CI excludes), and reverted it on the advisor's advice: it is out of scope, and those 500s are pre-existing and local-only.

## Scott items

- Unscripted fake dispatch semantics (option b, T-SEED-20): should the fake refuse unscripted skills at once? That would rewrite T-SEED-20.
- FYI for P1-11: `swap_item` opens a second connection inside the request transaction, which can deadlock unseen against AccessExclusive operations.
- J3 vs SEED/P2-08 untrusted-block conflict (above, options a/b/c).
- A2.2's last step needs a status on the Tasks-view row (above, options a/b).
- Markers to remove: T-SEED-27 and T-SEED-29 (above).
- Local only: after the load set (A0.6), a reset's TRUNCATE deadlocks with old-world deliveries that insert into the outbox, and the reset returns 500 after 3 tries. CI excludes A0.6/P0-29.

## Verify

```bash
bash /tmp/claude-1002/SEED-impl-2-c5/check.sh   # fix the cd path first
timeout 120 gh pr checks 138 --repo SpaceshipCreative/tumnis-guide
```
