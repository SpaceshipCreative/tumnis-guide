# HANDOFF: P1-13 Review queue v1 (continuation 2)

Stopped on the coordinator's "HANDOFF NOW" (context watcher). The implementation is finished and green locally. The PR is open, and the CodeRabbit review loop has not started yet.

## State

- **PR:** #80, https://github.com/SpaceshipCreative/tumnis-guide/pull/80 (`[P1-13] impl: Review queue v1`). The body is in `/tmp/claude-1002/p113c1/pr-body.md`; it lists the deviations, per-layer results and Context7 citations. `@coderabbitai review` was requested once, just after opening.
- **Branch:** `wp/P1-13`. Push with `/usr/bin/git push origin HEAD:wp/P1-13`. main was merged (b7dcb15, clean).
- **Commits:**
  - `8b91a89` test(tasks): P1-13 spec tests (red)
  - `bdd057e` feat(tasks): review order, downstream and Jev factor rules. HANDOFF-rules.patch was applied and deleted in this commit.
  - `74ba997` feat(tasks): review queue, decide endpoint and blocking-impact projection
  - `92b148f` feat(frontend): review queue screen with keyboard decisions (T-12..14 unmarked)
  - `136b51b` fix(tasks): the decide route uses `/v1/review/{id}/decide`. The A0.3 sweep maps `{id}` after review.
  - `916dc81` test(tasks): T-05..10 unmarked; `test_review_rules.py` added
  - (merge of origin/main)
  - `ebb4878^` style(frontend): prettier on the spec file (whitespace only). This commit also deleted the old HANDOFF.md.
  - `ebb4878` chore: remove the handoff notes. It is an empty commit, because the deletion landed in the style commit.
  - (this commit) chore: P1-13 handoff
- **Markers:** all 14 spec markers are removed, and every spec test passes locally.
- **Alembic revision:** `tasks_0004` (down `tasks_0003`). P2-13 (#68, open) also uses tasks_0004; whichever merges second re-chains to `tasks_0005`.

## Local results

| Layer | Result |
| --- | --- |
| `make check` | green (backend unit 1164 passed; frontend lint, typecheck, Vitest 56 files / 104 tests) |
| `make test` (unit + contract) | 1522 passed |
| `make test-int` | T-P1-13-05..10 pass. The only real failure, A0.3 on the decide route, is fixed in `136b51b`. |

The remaining local integration failures are load and port collisions on the VM:
- MinIO/S3 and rclone containers: "address already in use"
- `test_poll_backstop` timing
- the kill-worker resume tests
- typeahead p95 latency

## Next steps

1. Run the review loop in ~/tumnis-coordinator/pr-review-loop.md on PR #80:
   - Poll `gh api repos/SpaceshipCreative/tumnis-guide/pulls/80/reviews` for coderabbitai[bot].
   - Read the inline comments and the review bodies.
   - Fix each comment or reply with a reason, then resolve the threads (GraphQL).
2. `gh pr checks 80`. For a failed job, `gh run view <id> --log-failed`. `preview` stays pending (expected). The known e2e flake is `POST /v1/test/reset` 500 (#56); rerun the failed job once.
3. After fixes: `make check` → commit → `/usr/bin/git push origin HEAD:wp/P1-13` → `gh pr comment 80 --body "@coderabbitai review"`.
4. If the coordinator says another PR merged: `/usr/bin/git fetch origin`, `/usr/bin/git merge origin/main`, then `make gen`, re-verify and push. If #68 merged first, rename the revision to `tasks_0005` (down `tasks_0004`, P2-13's).
5. When done, delete this HANDOFF.md in a chore commit.

## Deviations (also in the PR body)

1. `blocking_impact` stays text (squawk bans a type change in expand); it is cast to float8 in the order.
2. `rules.jev_factor` takes a structural ScoreLike and the route string, because tasks cannot import decisions. `tasks.api` re-exports it.
3. `.importlinter` has one test-only ignore: `tumnis.modules.tasks.tests.** -> tumnis.modules.decisions.api`.
4. `plan_issue` is not registered (P1-11 is unmerged), and neither are the P1-15 kinds.
5. `provisioning_failed` retry is a seam: the profile goes not_provisioned → provisioning. P1-06 adds the workflow.
6. `decision_unavailable` accept (re-ask) is not implemented, because the inputs are not stored (P1-07). Edit works.
7. The projection subscribers are `tasks.refresh_review_impact` (task.created), `tasks.refresh_review_impact_on_status` and `tasks.refresh_review_impact_on_update`. There are three because each subscriber takes one event.
8. The decide path is `{id}`, the plan's form, which A0.3 needs.
9. The seed does not include a review item of each kind: `tumnis/seed.py` has no review-item record. This is a follow-up.
10. Extra 422 code `invalid_snooze`.
11. The badge stays P0-23's `ReviewBadge.tsx`; there is no new InboxBadge.
12. The migration id may need re-chaining (see State).

## Shared-file edits

- `docs/IMPLEMENTATION-PLAN-DETAILED.md`: the A8 row for `review_item.added`.
- `frontend/src/lib/live-map.ts`: `tasksListReview` added.
- `backend/.importlinter`: the test-only ignore above.
- Generated files, via `make gen`.

## Scott items

- Confirm that deviation 9 (the review-item seed) can be a follow-up. Nothing blocking.

## Context7 docs checked

- SQLAlchemy 2.x (tuple_ and cast)
- TanStack Router 1.170 (validateSearch, useNavigate)
- TanStack Query 5.104 (invalidateQueries, setQueryData)
- Hypothesis (stateful)

## Verify commands

- Unit: `cd backend && uv run pytest -q -m "not integration and not contract" -n 3`
- Integration: `make test-int` (bare, from the worktree root). Contract: `make test` (bare).
- Frontend: `cd frontend && npx vitest run src/components/review src/lib/time.test.ts --maxWorkers=3`
- `make check`, with `SEMGREP_SETTINGS_FILE`, `SEMGREP_LOG_FILE` and `SEMGREP_VERSION_CACHE_PATH` under `/tmp/claude-1002/p113c1/semgrep/`.
- Scratch folder: `/tmp/claude-1002/p113c1/`. `unmark.py` lives there.
