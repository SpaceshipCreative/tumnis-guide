# P1-12 handoff 2 (Project Calendar view)

PR: **#77** https://github.com/SpaceshipCreative/tumnis-guide/pull/77, branch `wp/P1-12`
(push with `/usr/bin/git push origin HEAD:wp/P1-12`).

## State at handoff (cd269c7, before this HANDOFF commit)
- Everything is done. At cd269c7 the PR was MERGEABLE, every CI check was green (15 of 15;
  `preview` pending as expected, no homelab runner), and CodeRabbit had **0 unresolved threads**.
- The coordinator asked for "77 MERGE-READY at <sha>". This HANDOFF commit adds a new head,
  so the next agent must: delete HANDOFF.md in a `chore:` commit, push, wait for CI green,
  confirm 0 unresolved threads and MERGEABLE, then report `77 MERGE-READY at <that sha>`.
- If main moves and the PR conflicts: `/usr/bin/git fetch origin && /usr/bin/git merge origin/main`.
  Conflicts so far were only in generated files (fix with `make gen`) and in
  `backend/tumnis/modules/projects/api.py` (keep both sides).

## Commits (this continuation)
- 06ec011 view renders its region once loaded, keepPreviousData on week change, ESLint fix; T-01 marker off
- 494a33b, d4fc497, d529fc2, 021449f: T-02..05 markers off (one per commit)
- 7caeb0a PATCH looks up the task before the block check (A0.3 got 422 instead of 404);
  `_isolation.py` treats a required id-filter query param as a row read
- 071b180, 62685b5: T-06 and T-08 markers off (XPASS(strict) locally, green in CI)
- 1cb00c4 removed the first HANDOFF.md
- a2b4319 `plan_items.position` integer (squawk rejected smallint)
- 209f448 rollback restores the mutation's own week key (CodeRabbit), plus `CalendarView.rollback.test.tsx`
- 1c2cf7d trashed or purged tasks' blocks are not planned time: `tasks.api.live_task_ids`, plus
  `planning/tests/integration/test_trashed_task_blocks.py` (CodeRabbit)
- 3551d4b planningGetProjectWeek added to live-map `project.lists`; 422 detail wording (CodeRabbit)
- 8b62ae0, a3ea2fb, 328e7ed, cd269c7: merges of origin/main

## CodeRabbit (all 4 threads resolved)
1. Trashed tasks' blocks still counted: fixed 1c2cf7d.
2. Rollback wrote to the wrong week after a week change: fixed 209f448 (test red on the old code, then green).
3. Inverted 422 detail text: fixed 3551d4b.
4. Link edits didn't refresh the week: fixed 3551d4b.

## Deviations and Scott items
All are in the PR body (13 numbered deviations). Main points: the plan's
`PATCH /v1/plan/{day}/items/{task_id}` instead of a tasks PATCH; `daily_plans`/`plan_items`
created ahead of P1-11 (Part A shared names; `planning_0002`, down `planning_0001`;
`position` integer, not smallint); keyboard via the view's own slot cursor; `FitOfferRow`
sharing deferred to P1-11. Scott items: none blocking. Decision 5 is not hard-coded.
Shared-file edits: none of the listed ones; `backend/tests/acceptance/_isolation.py` (helper
only) and `row_factory.py` COLUMN_VALUES.

## Local test notes
The VM ran at load ~90. `make check`'s Vitest step hit 20 s timeouts in files this PR
doesn't touch, and they pass when run alone. The one local `make test-int` re-run was lost to
a Docker port collision with another agent. CI is the authority, and every layer is green there.

## Verify commands
- `gh pr checks 77`
- `gh api graphql -f query='query{repository(owner:"SpaceshipCreative",name:"tumnis-guide"){pullRequest(number:77){mergeable reviewThreads(first:50){nodes{isResolved}}}}}'`
- `cd frontend && npx vitest run --maxWorkers=3 src/components/project/CalendarView.test.tsx src/components/project/CalendarView.rollback.test.tsx`
- `cd backend && uv run pytest -q -n 3 -m "not integration and not contract" tumnis/modules/planning`
