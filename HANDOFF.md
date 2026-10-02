# HANDOFF: JOURNEYS-B (J2 A1.1, J3 A2.1, J8 A2.6), continuation c3 next

Instructions: ~/tumnis-coordinator/prompts/wave1/JOURNEYS-B.txt (binding). Branch `fix/journeys-run-focus`.
Push with `/usr/bin/git push origin HEAD:fix/journeys-run-focus`. PR **#161**
(https://github.com/SpaceshipCreative/tumnis-guide/pull/161), now READY (not draft). CodeRabbit review requested
once (comment 5946935994); its first review (5389040386) arrived. origin/main merged up to 0973da9 (#164, #163);
`make gen` clean after it. PR body is current except the review-round notes (source: /tmp/claude-1002/JOURNEYS-B-c2/pr-body.md).

## All three journeys pass, all markers off
| Journey | State |
| --- | --- |
| J3 / A2.1 | Passes; marker off in 7e070980 (CI run 36962009623). |
| J2 / A1.1 | Passes; marker off in d769987f (CI run 36971618979, "Expected to fail, but passed" on phone). CI run 36973900377 then ran it unmarked: green on laptop and phone. |
| J8 / A2.6 | Passes; marker off in 36952f2c (CI run 36973900377, "Expected to fail, but passed" on laptop and phone). |

## Commits this continuation (c2), oldest first
- `a02a2e5b` feat(frontend): Just added on the dashboard and the drawer's History (A1.1). JustAdded.tsx (dashboard
  left column, first), TaskLabel in LabelChip.tsx, TaskHistory.tsx, lazy TaskDrawer on `/` (`?task=`, `?run=`),
  justAddedQuery in loader, msw handlers. Tests T-JOURNEYS-B-01..04.
- `dc017ce1` fix(core): the test clock waits for the writes in flight (A2.6, J8). core/testing_writes.py
  (WritesInFlight + pure-ASGI middleware), app.py gated block (`tumnis_adapters == "fake"`), testing_routes.py:
  `writes_settled` dependency on `@router.post("/clock", dependencies=[...])` (set_clock body untouched; #164's
  body change merged cleanly). Integration test test_test_clock_waits_for_writes.py (red first: write read 10:25).
- `d769987f` A1.1 marker off; `8e5374a1` merge origin/main (#164, #163); `36952f2c` J8 marker off;
  `5dd9996c` chore: removed the old HANDOFF.md; `4f8202af` review fixes (History infinite query + Load more,
  T-JOURNEYS-B-05; clock test waits on an asyncio.Event). make check passed before each commit.
- (this commit) chore: JOURNEYS-B handoff.

## CodeRabbit review 5389040386: thread status (none replied to or resolved yet)
| Thread | Comment | Action |
| --- | --- | --- |
| PRRT_kwDOUx-vCc6oQHBr | 4163509697 TaskHistory.tsx: paginate history | FIXED in 4f8202af. Reply + resolve. |
| PRRT_kwDOUx-vCc6oQHBg | 4163509683 clock test: Event, not a 50 ms sleep | FIXED in 4f8202af. Reply + resolve. |
| PRRT_kwDOUx-vCc6oQHBb | 4163509677 clock test: build httpx client via tumnis.core.net | DECLINE: AGENTS.md's rule is for outbound clients; semgrep `tumnis-raw-httpx` excludes `**/tests/**`; 12 test files build the in-memory ASGITransport client the same way (e.g. core/tests/integration/test_testing_routes.py). Reply + resolve. |
| PRRT_kwDOUx-vCc6oQHBk | 4163509690 focus/testing.py: raise when `_settle` times out | DECLINE (suggested reply): `_unsent()` counts every unsent outbox row in every workspace, not only focus deliveries, so a timeout doesn't prove the focus session is missing; failing the tick on an unrelated slow event would make the e2e ticks flaky. The log line records the timeout. Reply + resolve. |
| (review-body nitpick, no thread) | test_focus_wake_settles.py: make the delivery/tick interleaving deterministic | Decline in the final report: the test was red before f00366e3 (the race happens in practice), and a test checkpoint in the relay would add product hooks for a test. |
Reply via `gh api repos/SpaceshipCreative/tumnis-guide/pulls/161/comments/<id>/replies -f body=...`; resolve with
GraphQL `resolveReviewThread(input:{threadId:"<PRRT id>"})`. Dump script: /tmp/claude-1002/JOURNEYS-B-c2/cr-dump.py.
After pushing 4f8202af+handoff, comment `@coderabbitai review` once for the re-review (pr-review-loop step 6).

## CI
- Run 36975305441 (head 5dd9996c): all green except e2e: only T-P0-24-05 (board keyboard drag, laptop) failed. That
  test ALSO fails on main (main run 36974649831 at 0973da9, e2e job 110735816248) and failed once on phone in run
  36971618979: pre-existing on main after #163/#164, not this PR. Tell the coordinator; don't fix it here.
- Head 4f8202af not yet CI'd (push this handoff to trigger). preview stays pending (ignore).

## Next steps (c3)
1. Push (if not already), check CI on the new head (`/tmp/claude-1002/JOURNEYS-B-c2/wait-ci.sh` skips preview).
2. Reply to and resolve the four threads above; request `@coderabbitai review` once; loop until no open threads.
3. When CI is green apart from main's T-P0-24-05 (or that's fixed on main; merge origin/main then), send
   "#161 MERGE-READY at <sha>" to main via SendMessage, noting T-P0-24-05 is main's.
4. Delete this HANDOFF.md in a chore commit at the very end.

## Findings to report (not fixed here)
- `frontend/src/components/project/ActivityView.tsx` passes `initialPageParam: null` to the generated infinite query;
  the generated queryFn reads `null.body` and throws (the same thing broke TaskHistory until 4f8202af). Likely a real
  bug in the project Activity view; follow-up for its owner.
- `/v1/test/reset` doesn't cancel pending focus_plan/focus_session workflows, so they hear later tests' ticks.
- Local A1.1 on this VM: the label took ~1.6 s (host load ~27); CI does it within 1 s. Environment, not product.

## Docs (cited in the PR body)
Context7 `/tanstack/router` (pinned 1.170.40) for validateSearch with zod and navigate search updaters;
Context7 `/dbos-inc/dbos-docs` client.md (list_workflows_async), dbos 3.1.0.

## Denials and Scott items
- c2: no permission denials. Docker commands ran with dangerouslyDisableSandbox (the sandbox blocks the socket).
- No Scott items. Decision 83 is the coordinator's default ("not yet planned or done" read as "still in Backlog",
  stated in the PR body); Scott may override.

## Local tools (decision 76; scratch only, never committed)
/tmp/claude-1002/JOURNEYS-B-c2/: check.sh (make check with semgrep paths), run-int.sh (integration files),
stack.sh up|down (private stack tumnis-jb on :18983; currently DOWN), diag.sh, diag-a11-relaxed.sh, reqs.py.
