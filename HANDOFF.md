# HANDOFF: JOURNEYS-A (J1, J6, J7 planning journeys)

Instructions: ~/tumnis-coordinator/prompts/wave1/JOURNEYS-A.txt (binding). Branch
`fix/journeys-planning` (pushed from this worktree with
`/usr/bin/git push origin HEAD:fix/journeys-planning`). The draft PR is NOT opened yet. Open it
next (title `fix(acceptance): planning journeys J1, J6, J7 pass`). Cite APP-05 in the body.

## Commits on the branch (on top of main 67a43da6)
- 494b1547 `test(acceptance): J7 helper drives the AI task through a run` (frontend/e2e/phase1.ts,
  helper only, no test body touched).
- 155a58ee `chore: JOURNEYS-A handoff` (this file), then a follow-up adding these SHAs.
- `make check` passed before these commits (backend 1865 passed; daemon, profiles and frontend
  lint, typecheck, Vitest and bundle tests all green).
- No PR yet, so no CI run and no CodeRabbit threads. No test.fail() markers removed.
- The tumnis-ja stack (port 18931) is STILL RUNNING. Reuse it or tear it down.

## Local stack and diagnostics
- My stack: `docker compose -p tumnis-ja --env-file /tmp/claude-1002/JOURNEYS-A/compose.env -f <worktree>/deploy/compose.test.yaml up -d --wait --build`.
  The api is on http://localhost:18931 (use `localhost`, not 127.0.0.1: Playwright's request
  context does not send the `__Host-` Secure cookies to 127.0.0.1, and every API call 401s).
  Tear down with `docker compose -p tumnis-ja -f <worktree>/deploy/compose.test.yaml down -v`.
- Throwaway diag copies (Scott decision 76; never commit) are in /tmp/claude-1002/JOURNEYS-A/diag/:
  `playwright.diag.config.ts` goes in `frontend/`, and `*.diag.spec.ts` in `frontend/e2e/_diag/`.
  Run: `frontend/node_modules/.bin/playwright test -c frontend/playwright.diag.config.ts J7`
  with the sandbox off (the sandbox's network namespace refuses loopback). Move them out of the
  tree again before `make check` and before staging. Stage by explicit path only.
- `make check` locally needs SEMGREP_SETTINGS_FILE / SEMGREP_LOG_FILE /
  SEMGREP_VERSION_CACHE_PATH under $TMPDIR (~/.semgrep is read-only).

## Findings per journey

### J7 (A1.6), partly fixed
1. FIXED (helper): `arrangeCloseTheDay` walked the AI task Today -> In review as the human. That
   is 409 `transition_not_allowed` by design (FR-5.8: only an agent posts a result; tasks/rules.py
   TRANSITIONS). The helper now scripts the fake runner for the AI task's title (a `result` step
   with an agent result link), POSTs `/v1/tasks/{id}/run` (the run moves Today -> In progress),
   polls until In review (1 s polls, advancing the frozen server clock 1 s per poll so the
   per-principal rate limit refills; at 250 ms it hit 429), then the human moves it to Done.
   Verified locally: Shipped (2 items) and the Agents finished title now pass.
2. OPEN: "1 task prepared by agents" fails. Two causes found:
   a. The enrichment run of "Send Acme the March invoice" ends `failed` (enrichment_status
      pending -> running -> failed, about 15 s after create). Not yet diagnosed: check the worker
      log for that run (script `runnerScript(ACME_AGENT, "enrich", "enrich__hybrid_invoice")`,
      recording backend/tests/fakes/recordings/runner/enrich__hybrid_invoice.result.json; the
      label is null with label_reason "Needs a signature", so check the label wait and the
      enrich result schema). Also, the helper returns before the enrichment finishes: it should
      wait until the task's enrichment_status is `done`.
   b. Clock trap: `day_summary` counts enrichment runs with `finished_at` inside the local day
      (planning/rules.py day_summary, `prepared`). The worker stamps run ends with SystemClock
      (agents/workflows.py `api.run_ended(..., now=SystemClock().now())`), i.e. the real date,
      while J7's day is 2026-03-09 (the api's test clock only). So even a succeeded enrichment run
      is not counted. Fixing this needs either the worker to follow `/v1/test/clock` in fakes mode
      (a cross-cutting core change that also affects groups B and C) or a different product rule.
      This probably needs a decision: message main with options before building it.
3. Not yet reached: Queued overnight, Rolls over (+1 tonight, rollover-count), the phone sheet
   height, and the Done/Escape no-writes check.

### J1 (A1.2), needs a decision (not yet sent to main)
- Step 3 races: J1.spec.ts lines 88-97 read `getTask(...).status` as soon as the row shows
  `data-state="accepted"`. The row shows accepted optimistically (planWrites.ts `accepted()`),
  which the locked Vitest test TodayPanel.test.tsx (lines ~343-361, "Optimistic: accepted before
  the server answers", gated MSW handler) requires. Observed: POST accept for the 2nd item
  completed at 07.841, and J1's GET of that task ran at 07.815 and read `backlog`. No product
  change closes this deterministically. Options for main/Scott: (a) a settle wait in J1 before
  the status reads (like decision 44); (b) drop the optimistic assertion in TodayPanel.test.tsx;
  (c) leave it (flaky by design). SEND THIS TO MAIN.
- In the diag copy, add `expect.poll` on the two statuses to get past step 3 and find the later
  J1 defects (Swap picker, Remove, reload), and report them all in one message.
- Steps 1-2 passed locally (4 items, free blocks, chips, reasons, blocks inside free blocks).

### J6 (A1.3), to build (coordinator: APP-05 is ours; cite APP-05 in the PR body)
- Fails at `POST /v1/test/fakes/calendar.google/script` -> 404 (no scriptable calendar fake).
- Planned design (advisor-reviewed):
  - `register_fake_script("calendar.google", parse)` in calendar/adapters/__init__.py. The
    parser takes `{"scenario": "<name>"}` and stores that scenario's events.list pages per
    calendar id (a scenario file next to the recordings, e.g.
    calendar/tests/recordings/google_calendar/scenarios/no_ninety_minute_gap.json; check that
    path ships in the image).
  - `FakeGoogleCalendar.list_events` consults `fake_scripts.lookup("calendar.google")` (enabled
    in the worker in fakes mode) before its recordings.
  - calendar/testing.py registers the test tick `calendar-sync`: enqueue `api.SYNC_WORKFLOW` on
    `api.SYNC_QUEUE` by name for every connected account (DBOSClient, like planning/testing.py),
    wait for them; the router imports it (`# noqa: F401`). Never import calendar.workflows from
    the api.
  - Accounts: connect the two fake Google accounts in the J6 helper `squeezeMondayCalendar`
    through the real OAuth path (PUT the `calendar.google` settings section with a client_id,
    `GET /v1/calendar/oauth/start`, `GET /v1/calendar/oauth/callback?state=..&code=code-a|code-b`,
    poll `/v1/calendar/accounts` until both are connected), then script and tick. Do not seed
    accounts (the worker's real */10 calendar-sync-tick would then pull recordings into groups
    B and C).
  - Verified: the connector does not drop items outside the sync window (the worker computes the
    window from the real clock; the fake ignores it).
  - Scenario: Monday (2026-03-09, America/New_York, default hours 09:00-18:00, seed busy 9-10,
    12-13, 15-15:30) must leave exactly ONE 60-minute free block and everything else under 60
    (J6 uses `free_blocks.find(b => b.minutes === 60)`), e.g. extra busy 10:00-11:00,
    13:00-14:30, 15:30-17:30. Tuesday must keep a >= 90 block (move_to 2026-03-10).
- After that: Split UI ("No 90-minute gap today", "Split 60 + 30", "Move to Tue 10 Mar",
  "Accept split"), then Re-plan placing the 60-minute subtask in the 60 block. Untested.

## Next steps, in order
1. Open the draft PR. Push often.
2. Message main about J1 step 3 (options above) and the J7 clock trap (2b) with options.
3. Diagnose J7 2a (failed enrichment) and make the helper wait for it.
4. Build J6 (APP-05) test-first: a unit test for the parser, an integration test for the tick
   and the fake reading the stored scenario; Context7 for DBOS DBOSClient.enqueue_async and the
   SQLAlchemy calls.
5. Markers come off only after CI shows "Expected to fail, but passed" (decision 78), citing the
   run id. Then `gh pr ready`, one `@coderabbitai review`, and the review loop.
6. Tear down the tumnis-ja stack when done.

## Scott items
- J1 step 3 conflict between two locked tests (J1 vs TodayPanel.test.tsx optimistic accept).
- J7: the worker does not follow the test clock, so "prepared by agents" (runs finished today)
  cannot count on the test day.
