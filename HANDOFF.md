# P1-07 handoff (c2 -> c3): Quick-add labels and overrides

**PR #93** is open (https://github.com/SpaceshipCreative/tumnis-guide/pull/93). Push with `/usr/bin/git push origin HEAD:wp/P1-07`. From a fresh throwaway worktree: `/usr/bin/git fetch origin`, `/usr/bin/git merge origin/main`, then `/usr/bin/git merge origin/wp/P1-07`.

## Commits by c2 (on top of c1's 111b742)

- `6ce5ff9` perf(decisions): the relay starts the quick-add label directly (T-01 unmarked).
  - New `core.events.subscribe(..., direct=True)`: the relay runs the handler itself, outside any step, and falls back to a queued delivery if it fails.
  - The label subscribers are direct. `workflows.start_label` calls `DBOS.start_workflow_async` under `SetWorkflowID("label:<event_id>")` and no longer reads the task first.
  - The `decisions` queue is gone; `worker.py` is back to main's version.
  - `LabelChip.test.tsx` was reformatted by Prettier (whitespace only; the file is not locked on main).
- `669c17d` fix: the CodeRabbit round.
  - `DIRECT_TIMEOUT_S` = 2 s on direct handlers.
  - `start_label` shields the workflow start.
  - `set_ai_label` and `set_label_suggestion` take `for_title`. New test `decisions/tests/integration/test_label_stale.py` (red, then green).
- This commit: HANDOFF.md.

## CodeRabbit

- Round 1: 3 threads, all replied to and resolved.
  - Fixed: the timeout and the version/title check.
  - Declined: the `isPending` guard in `useLabelOverride`, because it fails spec test T-12.
- `@coderabbitai review` has been requested again after 669c17d. Check for new comments.

## CI (run on 6ce5ff9; 669c17d is running)

- Everything green except:
  - `integration`: T-P0-20-03, plus T-01 at p95 1,062 ms.
  - `security`: known OpenSSL CVEs; `fix/openssl-cves` owns it.
  - `preview`: pending, as expected.

## T-01 latency facts

- Traced stages (40 quick-adds, local):
  - commit to relay: 122 ms
  - relay to workflow start: 17 ms
  - load: 88 ms
  - decide: 407 ms (250 ms is the fake)
  - apply: 48 ms
- Uncontended (creates spaced 1.5 s apart): about 450 ms.
- Under the burst, the process uses about 1.2 CPU cores, so it is GIL-bound.
- The label workflow on the DBOS loop was much slower, because that loop is congested by events-queue deliveries.
- An idea not built (it would need Scott or the coordinator): run label workflows on a dedicated event-loop thread, away from the test's blocking `owner_query` poll on the main loop. Another option is pooled engines in the `dbos` fixture.
- Running a single Docker test locally: temporarily set the `test-int` recipe (Makefile line 40) to `$(BACKEND) uv run pytest -q -n 0 -m integration <path> > /tmp/claude-1002/P1-07-c3/out.txt 2>&1; grep ... out.txt`, run bare `make test-int`, then restore with `/usr/bin/git show HEAD:Makefile > Makefile`. Never commit it.

## Next steps

1. Watch CI on 669c17d (`gh pr checks 93`) and CodeRabbit's re-review. Fix or decline any new comments; resolve the threads.
2. The integration job stays red until Scott decides T-P0-20-03. Don't send MERGE-READY while it is red. When fix/openssl-cves or #80 merges, merge origin/main, re-verify and push. If #80 merges first, re-chain `tasks_0006` after `tasks_0005`, and wire `apply_review_decision(..., label_override=False)`.
3. Delete HANDOFF.md in a chore commit when done.

## Deviations (in the PR body)

1. A1.1 `test.fail()` kept.
2. A1.4 step 3 xfail kept (no "Acme site" in the seed).
3. No decisions queue: the plan's fallback.
4. No "Just added" list.
5. The seed has no `triage.reserved_judgments`.
6. `set_ai_label` and `set_label_suggestion` take a session and `for_title`.
7. `decisions.api.use_providers` test override.

## Scott items

- T-P0-20-03 against the AI label's version bump. The coordinator has passed it to Scott; don't touch the test.
- T-01's thin CI margin (in the PR body, "For Scott").
