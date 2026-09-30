# P1-07 handoff (c1 -> c2): Quick-add labels and overrides

c1 stopped on the coordinator's HANDOFF NOW at about 354k tokens. No PR is open yet.

## State

- Branch: push with `/usr/bin/git push origin HEAD:wp/P1-07`. From a fresh throwaway worktree: `/usr/bin/git fetch origin`, `/usr/bin/git merge origin/main`, then `/usr/bin/git merge origin/wp/P1-07`.
- Commits (on top of main at 260d18d):
  - `81ace5b` test(decisions): P1-07 spec tests (red), by c0.
  - `af984bc` chore: P1-07 handoff, by c0.
  - `8fb3a95` feat(tasks): label rules and whitelisted label inputs (T-08, T-14 unmarked).
  - `a02cde8` feat(decisions): label_task workflow, label columns and AI label writes.
  - `4b4d2a3` feat(frontend): label chip with override and session undo (T-10..13 unmarked), plus the A1.4 `_phase1.py` helper fixes.
  - The next commit (this handoff) contains:
    - The T-02/03/04/05/06/07/09 markers removed. All seven XPASS(strict) in the full `make test-int`.
    - The `tests/acceptance/conftest.py` `seed` override (it loads after `master_key_file` and `pepper_file`).
    - This file.
- CI: none yet. No PR, no CodeRabbit threads.

## Spec tests

| Test | State |
| --- | --- |
| T-08, T-14 (unit) | green, unmarked |
| T-10, 11, 12, 13 (Vitest LabelChip) | green, unmarked; the full Vitest run is 73 files, 148 tests passed |
| T-02, 03, 04, 05, 06, 07, 09 (integration) | XPASS in the full layer run, unmarked |
| T-01 latency (`test_label_latency.py`) | STILL RED (xfail kept); see Blocker 2 |
| A1.4 step 3 (`test_jev_and_vllm_down_sends_label_to_review`) | STILL RED (xfail kept); see Blocker 3 |
| A1.1 (Playwright) | `test.fail()` stays by coordinator decision |

## Last `make test-int` (full layer, before the markers came off)

11 failed, 996 passed, 8 xfailed, 1 error.

- 7 of the failures were our XPASS(strict) tests, now unmarked.
- 3 were VM noise: `test_rclone_copy_keeps_files_removed_at_source` (a known local-only failure), `test_typeahead_latency_on_load_fixture`, and `test_master_key_file[256]`. The S3 SSRF setup error was a Docker 500. Check these against main CI.
- 1 was a real regression: `search/tests/integration/test_index_events.py::test_task_changes_reach_index_through_events` (T-P0-20-03). See Blocker 1.

## Blocker 1: a locked-test conflict (Scott item; NOT sent yet, send it first)

The label writes bump `tasks.version`, as the plan says `set_ai_label` must, and the `app.touch_row` trigger bumps it on every UPDATE, including `set_label_suggestion`. T-P0-20-03 (locked) creates a task, drains the relay and workflows (which now label it), then PATCHes the title with the creation version, so it gets 409 `stale_version`.

The advisor said not to engineer around this. Send it to the coordinator (to `main` via SendMessage) as a Scott item with these options:

- (a) A spec-change PR. T-P0-20-03 reads the task's current version before the rename (a one-line test change). Recommended.
- (b) Make AI label writes version-transparent to human edits. This is a concurrency rule change touching update, status, move, trash and R-19. It is WP-sized.
- (c) Whatever Scott prefers.

Product note: with Jev on, any client write within about 1 s of creation that uses the creation version gets a 409 until the /ws refetch lands, and P1-08 widens that window.

Before sending, grep for the same pattern elsewhere: tests using `session_client` + `dbos` that write with a creation-time version after `drain`/`quiesce`/`relay_once`, and Playwright journeys that quick-add and then act with a list-held version. List the hits in the same message.

## Blocker 2: T-01 misses the budget badly

In a local `-n 0` run, the latencies went from 2.6 s up to p95 about 9 s: 40 creates, growing backlog.

Traces from temporary prints (removed) show:

| Stage | Time |
| --- | --- |
| Workflow, `wf_start` to `applied` | about 0.5 to 0.7 s |
| `load` step | 25 to 100 ms |
| `decide` step (250 ms fake plus about 5 DB round trips: `get_provider_config`, `local_decisions_only`, 2 `get_setting`, cache, threshold, log insert) | 350 to 450 ms |
| `apply` step | about 100 ms |
| `enqueued` to `wf_start` (decisions queue hop) | 60 to 230 ms |

The big cost is upstream: from `created` to the subscriber's `handler_start` (relay, then the events queue at 8 concurrency and 0.2 s poll). Each task makes 3 events (`task.created`, `decision.made`, `task.updated`), and every subscriber delivery is a workflow, so the backlog grows.

Ideas, in order. Measure each; never touch the test.

1. Check the relay wakes on NOTIFY for each commit. `relay_running(poll_s=1.0)`: is there a NOTIFY trigger on outbox inserts? Check `core/outbox.py` and the migrations.
2. Plan fallback (plan's implementation notes): start `label_task` from the relay directly (`DBOS.start_workflow`) instead of via the subscriber and events queue. Or have `label_on_create` enqueue with a higher priority.
3. Raise the events queue concurrency and poll rate only if needed (a shared core file: edit minimally and list it).
4. Also measure in CI (the done checklist wants 3 green CI runs); the VM has about 9 agents.

## Blocker 3: A1.4 step 3 is blocked by the seed

Sign-in now works (the acceptance conftest `seed` fixture loads after the master key and pepper). The test then fails with `LookupError: 0 projects named 'Acme site'`. The seed (`fixtures/seed/projects.yaml`) has no "Acme site", and neither does wp/P1-06. The plan's phase 1 seed additions (P1-04 and P1-06) own that. Keep the xfail and record it as a deviation.

## Decisions taken (coordinator approved; c0's list still holds)

1. Prove A1.1's label step at the API layer (T-01, T-02), in Vitest (T-10..13) and with A1.4 step 3. Leave A1.1's `test.fail()`. Record this as a deviation (the shared fake-script hook, branch fix/test-fakes-script-hook, and P1-08 are pending).
2. `decisions.api.use_providers(Providers | None)`: a process-level override. `decide(providers=None)` uses it before `_production_providers`. Tests reset it (autouse fixtures, and the acceptance conftest).
3. Enqueue from the subscriber in a fresh context: `asyncio.create_task(..., context=contextvars.Context())`. dbos 3.1.0 `DBOSContext.create_start_workflow_child` asserts it is not in a step. Workflow ID `label:{event_id}`, dedupe `label:{task_id}:{version}`. `enqueue_label` reads the task (skips it when gone or when the user chose the label) for the version.
4. The `decisions` queue: `worker.register_queues`, limiter 1200/60 s, `polling_interval_sec=0.1`. Constants are in `decisions/workflows.py`. Context7 `/dbos-inc/dbos-transact-py` confirms the kwargs.
5. Review kinds: `low_confidence_label` is registered in `tasks/review.py`, with names and payload identical to #80 minus `action_payloads`. An `unknown` winner goes to `decision_unavailable` (dedupe `decision:quick_add_label:<task>`), added in `apply_label` when provider != none.
6. Migration `tasks_0006` (`0006_label_columns.py`), chained after `tasks_0004`, `phase = "expand"`. Re-chain after `tasks_0005` if #80 merges first.
7. Undo:
   - `_record` adds `label_source` when the label changed; `undo_task` restores it.
   - `set_ai_label` writes its own change row as actor `system`.
   - `get_task` returns the AI change's `change_id` while `label_source` is jev or fallback, the change is not undone, and `task_version == version`.
8. `set_ai_label(s, task_id, *, label, source, reason, confidence, decision_id, now)` and `set_label_suggestion(s, ..., probabilities=...)` take a session. `set_label_suggestion` adds the review item itself (tasks owns the kind).
9. Override:
   - `update_task(..., label_override=True)`: a HUMAN actor changing a label the AI decided (`label_decision_id` or `label_suggestion` set, source not already `user`) emits `human.decided`. Its payload is `{item_kind: label_override, ..., payload: {value, overridden}}`.
   - It closes open `low_confidence_label` items as `superseded`.
   - `decisions.record_outcome` uses `payload.overridden` when present.
   - A label set by a person also clears `label_suggestion`.
   - **After #80 merges**, its `tasks.apply_review_decision` must pass `label_override=False` to `update_task`. Whoever merges second wires it. Tell the coordinator.
10. The triage setting is registered as section `triage` (`TriageSettings.reserved_judgments`, at most 10 items of 120 characters) in `decisions/api.py`. **Deviation:** the seed does not set `["pricing", "hiring"]`, because the seed loader has no settings kind.
11. UI:
    - `LabelChip` (presentational) with `useLabelOverride` (local optimistic pick plus `useUpdateTask`).
    - `TaskLabelChip({taskId})` (the task query, and an undo entry via `lib/undo.remember`, labelled "AI label").
    - `TaskRow` shows the chip for non-pending rows.
    - **Deviation:** there is no separate "Just added" list in quick add. The just-saved row is the project Tasks row, which carries the chip. BoardCard and TodayItem still say "No label yet" (DS-01 area).
12. Shared-file edits so far: none (`Makefile` was edited only temporarily for local runs and has been restored). `worker.py` is core but not a listed shared file. Generated: `schemas/openapi.json`, `frontend/src/api/types.gen.ts`, `frontend/src/api/zod.gen.ts`. Also `frontend/src/test/msw/project.ts` (fields added to a factory row).

## Remaining steps

1. Send the Blocker 1 Scott item to the coordinator (SendMessage to `main`).
2. Blocker 2: fix the T-01 latency (measure, then pick the fallback); unmark T-01 once it passes.
3. `make check` (with SEMGREP_* env vars pointing under /tmp/claude-1002/P1-07-c2/), `make test` (unit and contract; not run yet this round), then `make test-int` once.
4. Push, then open the PR:
   - Title: `[P1-07] impl: Quick-add labels and overrides`.
   - Body draft: `/tmp/claude-1002/P1-07-c1/pr-body.md` (summary only). Add per-layer results, the deviations above, Blockers 1 and 3, and Scott items.
   - Cite the Context7 DBOS docs (`register_queue` kwargs, `SetWorkflowID`, `SetEnqueueOptions`), the dbos 3.1.0 `_context.py` step assert, and the TanStack Query v5 optimistic-updates guide.
   - End the body with a blank line and the 🤖 line.
   - Then run `gh pr comment <url> --body "@coderabbitai review"`.
5. Run the review loop (`~/tumnis-coordinator/pr-review-loop.md`). Once green and clean, send `#<PR> MERGE-READY at <sha>` to main. Note: main's `security` job fails on the OpenSSL CVEs until fix/openssl-cves merges. Don't touch the Dockerfile. Merge main after that fix lands.
6. Delete HANDOFF.md in a chore commit when done.

## Verify commands

- `cd backend && uv run pytest -q -m "not integration and not contract" -n 3`
- `make test-int` (bare, from the worktree root; about 13 min).
- To run a single Docker test locally, c1 temporarily edited the `test-int` recipe to `pytest -q -n 0 -m integration --runxfail -s <path>`, ran bare `make test-int`, then restored the Makefile. Never commit that edit.
- `cd frontend && npx vitest run src/components/common/LabelChip.test.tsx`

## Scott items

- Blocker 1: T-P0-20-03 against the AI label's version bump. Not sent yet.
