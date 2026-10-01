# HANDOFF: SEED impl-2 (acceptance seed runtime), continuation 0

Branch `wp/SEED-impl-2`, based on main fb7d771. Push only with `/usr/bin/git push origin HEAD:wp/SEED-impl-2`. **No PR is open yet.** PR title when opened: `[SEED] impl-2: FakeAgent playback, master ready in fakes mode, planner tick`. Scratch folder: `/tmp/claude-1002/SEED-c0/`.

Binding instructions: `~/tumnis-coordinator/prompts/wave1/SEED-impl-2.txt`. Scott decision 51 applies: never edit or remove a journey's `test.fail()`/xfail marker to diagnose, not even locally. Read journey failures from CI logs instead.

## Commits

| SHA | What |
| --- | --- |
| 7fc626f | `test(agents)`: red spec tests. T-SEED-14..16 (unit, `backend/tumnis/modules/agents/tests/unit/test_fake_playback.py`), T-SEED-17..20 (integration, `backend/tests/harness/test_acceptance_runtime.py`), T-SEED-21 (Playwright, `frontend/e2e/acceptance/seed.spec.ts`, `test.fail()`). |
| 7d7d980 | `feat(agents)`: `adapters/fake.py` gains `load_recording` (bare file names only, from `backend/tests/fakes/recordings/runner`), `scripted_output` (resolves `title:` picks and alternates and the sentinel `task_id`), `phase_1_key`, `TASK_ID_SENTINEL`, `TITLE_PREFIX`, `UNKNOWN_TASK_ID`. T-SEED-14..16 green, markers removed. |
| 9b4ed3d | `feat(agents)`: `api.fake_runner_serves(transport, last_heartbeat_at)`, which is true only with fakes mode, the store enabled, a daemon profile and a runner that never heartbeated. `api.fake_served(profile_id, ctx=...)`. `master_agent` and `agent_for_project` return ready when it holds (`master_agent` now also selects `last_heartbeat_at`). `hermes.record_dispatch(s, packet, now=, dispatched=)` holds the runs-row upsert, the `dispatched` event and the run-events NOTIFY; `DaemonTransport.dispatch` uses it. `adapter_for` is unchanged on purpose, because P2-04's locked tests rely on it. T-SEED-17 keeps its marker until CI runs it. |

`make check` passed before each commit.

## Remaining TDD steps

1. **`agents/fake_play.py`: `dispatch_skill(ctx, packet, agent)`.**
   - Look up the profile name, then write the run row and event: `async with tenant_session(ctx) as s: await record_dispatch(s, packet, now=SystemClock().now(), dispatched={"profile": name, "skill": packet.skill, "runner_id": None})`.
   - Then `with use_workspace(ctx): await agent.dispatch(packet)`. This keeps the last-packet hook.
   - Then `fake_scripts.lookup(fake_scripts.RUNNER, phase_1_key(name, packet.skill))`. Validate the result with `Phase1Script` and ignore anything that isn't a phase 1 script.
   - If a script is found, start a background task with `loop.create_task(coro, context=contextvars.Context())`, so it doesn't carry the step's DBOS context, and keep it in `_playing`.
   - The background task:
     1. Sleeps `delay_ms`.
     2. Computes `output = scripted_output(load_recording(result), packet.model_dump(mode="json"))`. On ValueError, logs a warning without a body and answers nothing.
     3. Reads `runs.workflow_id`.
     4. Inserts a `result` run event (message_id `uuid5(run_id, "fake-result")`, on conflict do nothing) and sends a NOTIFY on `api.RUN_EVENTS_CHANNEL`.
     5. Calls `DBOS.send_async(workflow_id, {"type": "result", "run_id": ..., "status": "succeeded", "exit_code": 0, "output_json": output, "error": None}, api.run_topic(run_id), idempotency_key=str(message_id))`.
   - `load_recording` raises ValueError on JSON `null` (`plan__no_json`). Let it return None for null instead, so the run ends `no_json`, and update the `-> dict` type.
   - Without a script, the fake answers nothing: the run stays `running`, like a silent runner (T-SEED-20).
2. **`agents/workflows.py` `dispatch_step`.** After `_with_token`, if `await api.fake_served(task.profile_id, ctx=ctx)`, call `await fake_play.dispatch_skill(ctx, task, resolve("agents.hermes", "fake"))` instead of `DaemonTransport(...).dispatch(task)`, inside the same try/except. `resolve` is `tumnis.core.adapters.registry.resolve`, the instance `adapter_for` returns. Keep phase 1 playback out of the `dispatch_run` path.
3. **`planning/testing.py` (new; mirror `focus/testing.py`), imported from `planning/router.py`.**
   - `TICK_NAME = "planner-tick"`, `TICK_WAIT_S = 25.0` (module level; T-SEED-20 patches it).
   - `async def tick(client, now) -> int`:
     1. Loop over `audit.workspace_ids` (app session) and call `api.due_plan_day(ctx, now)` for each.
     2. For each due day, `client.enqueue_async({"queue_name": api.MAINTENANCE_QUEUE, "workflow_name": "build_plan", "workflow_id": api.plan_workflow_id(ws, day, "morning")}, str(ws), day.isoformat(), "morning", now.isoformat())`.
     3. Await each `handle.get_result()` under `asyncio.wait_for(..., TICK_WAIT_S)`. On timeout, return anyway.
     4. Return the number of builds started (T-SEED-18 expects `{"woken": 1}`, then `{"woken": 0}` on a second tick).
   - Use the workflow name as a string, so the api process does not import `planning.workflows`. `register_tick(TICK_NAME, tick)`.
   - Check that `build_plan` accepts `now` as an ISO string (it does: `datetime.fromisoformat`).
   - Then remove the `type: ignore[attr-defined]` on the `planning import testing` line in `test_acceptance_runtime.py`.
4. Update the `master_agent` and `agent_for_project` docstrings and the `fake_play` module docstring to describe the fake path.
5. `make check`, commit, push, and let CI run integration and e2e (Docker load rule: at most one local `make test-int` per continuation). Remove the T-SEED-17..20 xfail markers and T-SEED-21's `test.fail()` once each passes. If CI shows a strict XPASS, that means it passes.
6. **Journeys.** Only CI's "unexpected pass" on J1 (A1.2), J7 (A1.6), J8 (A2.6), J2 A1.1 or J3 (A2.1) licenses removing that journey's marker. A2.2's UI journey stays red: Scott decision 55 brings ask_human playback in a separate spec PR (`spec/decisions-54-55`). Wire A2.2 to it only when the coordinator says that PR merged.
7. **J6 (A1.3) harness.** It needs a `calendar-sync` test tick and a `calendar.google` `no_ninety_minute_gap` scenario hook. The coordinator says to add them here if they fit cleanly, otherwise on `wp/SEED-impl-3`.
8. **Open the PR.**
   - Write the body to `/tmp/claude-1002/SEED-c0/pr-body.md`. Include the per-layer results, the shared-file edits (none so far), the deviations below and the Scott items.
   - Cite the Context7 docs: `/dbos-inc/dbos-docs` python client `enqueue_async` and `get_result`, `DBOS.send_async`/`client.send_async` with `idempotency_key`. The installed DBOS 3.1.0 source (`dbos/_core.py send_bulk`) shows that a send from inside a step is a plain send.
   - End the body with a blank line and `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
   - Run `gh pr create --base main --head wp/SEED-impl-2 --title "[SEED] impl-2: FakeAgent playback, master ready in fakes mode, planner tick" --body-file ...`, then `gh pr comment <url> --body "@coderabbitai review"` once.
   - Then follow the review loop in `~/tumnis-coordinator/pr-review-loop.md`, and send `#<PR> MERGE-READY at <sha>` to main when the PR is clean.

## Decisions and deviations

- Playback is a fakes-only path. It runs only with fakes mode on, the store enabled (the compose.test api and worker), and a runner that never connected. Unit and integration runs that only select fakes keep the old offline behaviour, and T-SEED-17 checks both sides.
- The fake run writes the same `runs` row and `dispatched` event as a daemon dispatch, through the shared `record_dispatch`. It writes no mailbox row.
- An unscripted dispatch to the fake answers nothing, like a silent runner. The planner tick's wait is bounded.

## Scott items

- A2.2's UI journey: decision 55's spec-change PR (ask_human playback). Not built here.

## Verify

```bash
cd backend && uv run pytest -q -m "not integration and not contract" tumnis/modules/agents/tests/unit/test_fake_playback.py
SEMGREP_SETTINGS_FILE=/tmp/claude-1002/SEED-c0/semgrep-settings.yml SEMGREP_LOG_FILE=/tmp/claude-1002/SEED-c0/semgrep.log SEMGREP_VERSION_CACHE_PATH=/tmp/claude-1002/SEED-c0/semgrep-version make check
```
