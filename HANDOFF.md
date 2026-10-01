# HANDOFF: SEED impl-2 (acceptance seed runtime), continuation 1

**PR #138** (https://github.com/SpaceshipCreative/tumnis-guide/pull/138), branch `wp/SEED-impl-2`.
- Push only with `/usr/bin/git push origin HEAD:wp/SEED-impl-2`.
- Scratch folder: `/tmp/claude-1002/SEED-impl-2-c1/`. The PR body draft is `pr-body.md` in that folder (updated, see step 2).
- Binding rules: Scott decision 51 (never edit or remove a journey's `test.fail()`/xfail marker to diagnose); coordinator notes in `~/tumnis-coordinator/agent-reports/SEED-impl-2-coordinator-notes.md`.

## Commits (this continuation)

| SHA | What |
| --- | --- |
| dee4dd5 | `feat(agents)`: phase 1 playback and the planner tick. `fake_play.dispatch_skill(ctx, packet)` refuses an unknown or paused profile (AgentUnavailable). Otherwise it runs `record_dispatch` with `runner_id: None`, then looks up the `<profile>/<skill>` Phase1Script and starts background `_answer` in a fresh `contextvars.Context`. `_answer` sleeps `delay_ms`, applies `scripted_output(recorded_reply(...))`, writes the `result` run event through `api.record_run_event` (message id `uuid5(run, "fake-result")`), then calls `DBOS.send_async` to the run's `workflow_id` with an idempotency key. `workflows.dispatch_step` branches to it when `api.fake_served`. `adapters/fake.py` adds `recorded_reply` (None for JSON null); `load_recording` keeps its dict contract. `planning/testing.py` registers `planner-tick` (enqueue the due morning `build_plan` by name, wait up to the module-level `TICK_WAIT_S`) and is imported from `planning/router.py`. |
| 87d1b41 | Removed the T-SEED-17..20 xfail markers and T-SEED-21's `test.fail()`. All five XPASSed in CI run 36867792545. |
| c13e153 | `test(agents)`: T-SEED-22 red, `agents/tests/unit/test_seed_enrichment.py`. |
| db798b1 | `fix(agents)`: `events.enrich_on_create` skips `source == "seed"` (`SEED_SOURCE`). T-SEED-22 is green and its marker is removed. Why: seeded Acme tasks lacking criteria queued enrichments that hung on the unscripted fake and filled Acme's runs partition (2 at a time), so J3's `dispatch_run` never started. |
| e8db833 | Merged origin/main (#136 ask_human playback, #134, #102). Resolved the import conflicts in `fake.py` and `fake_play.py`, keeping both sides. |
| dbea9fb | Removed the old HANDOFF.md. This file re-adds it for the handoff. |

## CI state

- e8db833: every check passed except `integration`, which was still running at handoff, and `preview`, which stays pending as expected.
  - This handoff push cancels that integration run (CI concurrency), so read the next run.
  - e2e passed: T-SEED-11/12/21 green on both projects.
  - The journeys J1, J2 A1.1, J3, J6, J7, J8, A1.5 and A2.2 still fail as expected, and none passed unexpectedly. Their markers stay.
- CodeRabbit: one thread, on the old HANDOFF.md (comment id 4155836561), now outdated.

## Remaining steps

1. Wait for CI on the handoff commit. Integration must be green, with T-SEED-17..20 passing.
2. Update the PR body: `gh pr edit 138 --repo SpaceshipCreative/tumnis-guide --body-file /tmp/claude-1002/SEED-impl-2-c1/pr-body.md`. The draft already has the per-layer results, the T-SEED-22 seed guard (a shared-file edit in `agents/events.py`), the J6 split to `wp/SEED-impl-3`, the journey triage and the Scott items. Re-read it first.
3. Delete this HANDOFF.md in a `chore:` commit and push.
4. Reply on CodeRabbit thread 4155836561 that HANDOFF.md is removed and the PR body describes the as-built flow, then resolve the thread.
5. Send the journey triage to main with SendMessage (below).
6. When CI is green and no threads are open, send `#138 MERGE-READY at <sha>` to main.

## Journey triage (send to main; from CI run 36867792545 stack logs)

- **J1 (A1.2).** It gets through the tick, the four items and both accepts. `TodayPanel` `onPick` closes the Swap picker right after `action.mutate`. The test's `getPlan` (13:20:49.218) runs before the swap POST (13:20:50.25), so it fails at the swap. Candidate P1-11 fix: close the picker when the swap settles.
- **J2 A1.1.** The Jev label is applied in the worker about 300 ms after the POST, but the chip does not read Hybrid within 1 s. Suspect the live refetch (P1-07/P1-08 UI).
- **J3 (A2.1).** The runs-partition jam is fixed (db798b1). J3 still fails, and CI uploads no stack logs when e2e passes, so the next failure point is unknown.
- **J6.** Needs the calendar harness. Split it to `wp/SEED-impl-3`: a `calendar-sync` test tick plus the `calendar.google` `no_ninety_minute_gap` scenario.
- **A2.2.** `QUESTION_RUNS` already uses #136's step shape. It still fails, and its marker stays.

## Decisions and deviations

- Phase 1 dispatches skip `FakeAgent.dispatch` and its phase 2 hook. That hook counts `run` packets (J3 polls the count) and claims `task:<title>` plays.
- The fake's `dispatched` event has `runner_id: None`. `recorded_reply` returns None for JSON null.
- `adapter_for` is unchanged.
- Seeded tasks start no enrichment (T-SEED-22).
- J6 is split to `wp/SEED-impl-3`.

## Scott items

- **Unscripted fake dispatch semantics** (T-SEED-20): the fake records the run and answers nothing, so an unscripted enrich holds a runs-queue partition slot for the run timeout. Should the fake refuse unscripted skills at once instead? That would rewrite T-SEED-20.

## Verify

```bash
cd backend && uv run pytest -q -m "not integration and not contract" tumnis/modules/agents/tests/unit/
SEMGREP_SETTINGS_FILE=/tmp/claude-1002/SEED-impl-2-c1/semgrep-settings.yml SEMGREP_LOG_FILE=/tmp/claude-1002/SEED-impl-2-c1/semgrep.log SEMGREP_VERSION_CACHE_PATH=/tmp/claude-1002/SEED-impl-2-c1/semgrep-version make check
gh pr checks 138 --repo SpaceshipCreative/tumnis-guide
```
