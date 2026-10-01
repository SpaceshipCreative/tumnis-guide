# P2-15 handoff (Focus events engine and focus bar), continuation c2 -> c3

## State

- **#127** (backend, wp/P2-15): **MERGED** to main as d1b0424. Nothing left on it.
- **#128** (frontend, `wp/P2-15-impl-2`, "[P2-15] impl-2: focus bar, level chip and cadence",
  https://github.com/SpaceshipCreative/tumnis-guide/pull/128): open. The coordinator has
  retargeted it to **main**, and it is MERGEABLE. Push with
  `/usr/bin/git push origin HEAD:wp/P2-15-impl-2`. A fresh agent should merge
  `origin/wp/P2-15-impl-2` into its throwaway branch, which is at main.
- Scratch: `/tmp/claude-1002/P2-15-c2/` holds the PR body `pr-impl2-body.md` and helper
  scripts: `threads.sh <pr>` (unresolved threads), `reply.sh <pr> <comment-id>
  <body-file> <thread-id>` (reply and resolve), `wait_ci.sh <pr>`, `wait_review.sh
  <pr> <n>`.

## Commits on wp/P2-15-impl-2 (on top of the red commit baf852e)

- `b176d5d`: merges the red spec tests onto wp/P2-15.
- `387933a` feat(frontend): the focusSession machine; T-P2-15-16 unmarked.
- `a8f5f16` feat(frontend): FocusBar in AppShell; T-P2-15-17 (both tests) unmarked.
- `f2a3c68` feat(frontend): FocusLevel chip on the dashboard; FocusCadence in the
  Settings rail.
- `44c65e9` fix(frontend): review round 1. Changes:
  - failed replies reopen the check-in and show an alert;
  - stuck plus check-in now goes to checkIn;
  - FocusLevel shows an error alert;
  - cadence is validated as 5 to 240, with `noValidate`.
- `f9e7605`: merges wp/P2-15 (main with #120).
- This handoff commit.

## Review threads on #128

- Round 1, 4 threads: all fixed in 44c65e9 or declined, replied to and resolved. One was
  declined: it asked to edit locked T-17. A supplemental test covers it instead.
- **Round 2, still OPEN.** CodeRabbit reviewed f9e7605 at 06:44Z and left 2 threads:
  1. `PRRT_kwDOUx-vCc6n1RnK`, comment 4152566418, `FocusLevel.tsx`: clear the previous
     action's error when another action starts. Call `setLevel.reset()` /
     `less.reset()` before each `mutate`, or track the last error. Add a test.
  2. `PRRT_kwDOUx-vCc6n1RnQ`, comment 4152566429, `FocusCadence.tsx`: reject bad numeric
     input before treating the field as empty. A number input whose text is invalid
     ("e", "1e") reads as `""`. Check `event.currentTarget.elements` / the input's
     `validity.badInput` (use a ref), refuse it with the same alert, and add a test.
  Read the full comments: `bash /tmp/claude-1002/P2-15-c2/threads.sh 128`.
- CodeRabbit does NOT auto-review #128 while its base is non-default. Now that it targets
  main it may. Otherwise comment `@coderabbitai review` once after pushing the fixes.

## CI on #128 at f9e7605

All jobs are green. `preview` stays pending, which is expected. An earlier unit run
(f2a3c68) was cancelled at the 3-minute timeout: pytest hung at 99%. The next run passed,
so it looked like a flake.

## Remaining steps

1. `/usr/bin/git fetch origin`, then `/usr/bin/git merge origin/wp/P2-15-impl-2`, then
   `/usr/bin/git merge origin/main`. Main now has #127 (d1b0424). Keep both sides. Run
   `make gen` and check that the generated files are unchanged.
2. Fix the 2 round-2 threads (TDD), reply to each, and resolve them with `reply.sh`.
3. Run `make check` (SEMGREP_* env vars under `/tmp/claude-1002/P2-15-c3/semgrep/`),
   `npm --prefix frontend run typecheck` and the full Vitest suite. Then push.
4. Request a CodeRabbit review once if none comes. When it is clean and CI is green,
   SendMessage main: "#128 MERGE-READY at <sha>".
5. Delete HANDOFF.md in a chore commit before the final MERGE-READY push.

## Decisions and deviations (in the PR bodies)

From #127, already merged:
1. Workflow ids are `focus_plan:<ws>:<day>:<plan_id>` and `focus_session:<task>:<start>`.
2. `calendar.synced` is not subscribed.
3. The `focus_min_due_seconds` module seam.
4. `NoulAnswer` is defined in focus.
5. One session at a time.
6. The tick registry is in `core/ticks.py` (route `POST /v1/test/tick/{name}`).
7. `planning/tests/unit/test_plan_rules_edges.py` gained a fixed MAX_ITEMS case
   (`test_fallback_plan_stops_at_max_items`). The CI coverage gate had missed
   `planning/rules.py` line 429 when the planning integration helpers were collected
   with CI=true.

From #128:
1. The bar lists all of today's messages; earlier ones sit behind "Earlier today".
2. A Start button on a block's task, which A2.6 clicks.
3. Switched posts without `to_task_id`.
4. In the machine, `stuck` takes FOCUS_EVENT check-in or switched to checkIn (an
   addition to the plan's config).
5. The dashboard's `?focus=` search param is unused.

## Scott items

- A2.6 (J8) keeps `test.fail()`. It is blocked by the seed gap: it needs the
  2026-03-10 "Write proposal" plan, 10:00 to 10:50, cadence 25, and the runner recording
  `plan__write_proposal_block`.
- T-P2-15-18 waits for #103 (P2-03). When that lands, add `focus.responded` to
  `DIGEST_EVENTS` in `agents/rules.py` and unmark the test. A separate small PR is needed
  now that #127 is merged.
- Done-checklist items not built:
  - the "less of this" rate and "started within 15 min of block" on P1-18's metrics page.
    These need a backend metrics change; suggest a follow-up.
  - the phone-preview one-handed check, which needs the homelab preview.
- New core test route `POST /v1/test/tick/{schedule_name}`.
- The TaskDrawer findings from #120 are with the coordinator's fix agent. Leave them
  alone.

## Verify

- `cd frontend && npx vitest run src/machines src/components/focus src/components/dashboard/FocusLevel.test.tsx src/components/project/FocusCadence.test.tsx`
- `cd frontend && npm run typecheck`; then `make check`.
- `uv run --project backend python scripts/ci/spec_guard.py --base origin/main --head HEAD --labels ""`
