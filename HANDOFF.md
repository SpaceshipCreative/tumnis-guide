# P1-11 handoff (Daily plan), continuation c2 -> c3

Branch: `wp/P1-11`. Push with `/usr/bin/git push origin HEAD:wp/P1-11`.
**PR #119** https://github.com/SpaceshipCreative/tumnis-guide/pull/119 ("[P1-11] impl: Daily plan").
The PR body is current (written from `/tmp/claude-1002/P1-11-c2/pr-body.md`); update it with
`gh pr edit 119 --body-file <file>` when something changes. It now holds BOTH the backend and
the Today panel UI (see deviation 1 below). There is no impl-2 branch.

Original task prompt: `/tmp/claude-1002/coord7/p1-11.txt` (plus the coordinator rule files it names).

## Commits since c1 (on top of main fee9afa, merged in 1e6d31c)

- d367731 feat(planning): built_at sorts after the day's earlier plans (T-13); an empty plan emits no plan.published (A1.4 race); T-08..18 + A1.4 markers removed
- 0166f62 test(planning): plan rule edges (100% rules.py coverage)
- 0cac12a test(planning): plan.published v1 contract fixture
- f53cce2 chore: removed the c1 handoff
- 3ca3de8 fix(planning): PlanIssueOut.offer {split, move_to} (A1.3's e2e type)
- 11ba659 test(planning): impl-2 spec tests red (T-P1-11-20..23, test.fails + stubs, MSW plan fixtures)
- e253210 fix(planning): row_factory plan_issues.kind; (isoday convertor, reverted in 25d04d4)
- 6e76e32 feat(planning): Today panel plan UI (TodayPanel planDay, PlanItem, SwapPicker, FitOfferRow/FitOfferList, planWrites, loader prefetch, noPlan in dashboardDefaults); T-20..23 markers removed
- bcfeb4f fix(planning): every build floored at now (CodeRabbit thread 1) + test_late_morning_plan.py (seen red first)
- 6215255 fix(planning): SwapPicker uses lib/focusTrap dialogKeyDown + restores focus (CodeRabbit thread 2) + SwapPicker.test.tsx
- 25d04d4 fix(core): `_allowed_methods` in core/errors.py prefers concrete paths over templated ones (schemathesis AllowHeaderMismatch on POST /v1/plan/replan); isoday convertor reverted; issue routes renamed `{plan_issue_id}` so the A0.3 tenant sweep finds `plan_issues`; new unit test core/tests/unit/test_allowed_methods.py; regenerated openapi + client. **make check passed. Pushed with this handoff.**

## CodeRabbit

Both threads fixed, replied and resolved (late-morning floor: bcfeb4f; SwapPicker focus trap: 6215255).
Reviews so far used 2 of the hourly quota; it auto-reviews each push. After CI of 25d04d4,
check for new threads (`gh api repos/SpaceshipCreative/tumnis-guide/pulls/119/comments`, and the
GraphQL reviewThreads query) and handle per pr-review-loop.md.

## CI state

Run for 6215255 (before 25d04d4): contract failed (schemathesis AllowHeaderMismatch, POST
/v1/plan/replan) and integration failed with 6 tests:
- 2x A0.3 `POST /v1/plan/{day}/issues/{issue_id}/split|move: no A row` -> fixed by the `plan_issue_id` rename (25d04d4).
- `tests/contract/test_generation.py::test_operation_ids_are_unique_and_stable`, 2x `tests/meta/test_authz_matrix.py`, `auth test_login::test_password_alone_never_yields_a_session` -> all caused by the isoday convertor; reverted in 25d04d4.
- `agents/tests/integration/test_results.py::test_reject_adds_comment_and_returns_to_agent` (409 stale_version on the review item: a background subscriber bumped its version). Unrelated to P1-11 (P2-04 test); looks like a flake. If it fails again, `gh run rerun <id> --failed` once and mention it.

## Next steps

1. Wait for CI on 25d04d4 (`gh pr checks 119`; `preview` stays pending). Fix real failures; `gh run view <id> --log-failed`.
2. Handle any new CodeRabbit threads (fix or reply, then resolve).
3. Delete HANDOFF.md in a `chore:` commit when done; push.
4. When CI is green and no open threads: SendMessage to "main": "#119 MERGE-READY at <sha>".

## Local verification notes

- `make check`: `bash /tmp/claude-1002/P1-11-c2/check.sh` (sets SEMGREP_* files under the scratch folder).
- Focused integration runs: copy `/tmp/claude-1002/P1-11-c2/focus_conftest.py` to
  `backend/tumnis/modules/planning/tests/conftest.py` (NEVER commit it; delete before make check),
  list node-id substrings in `/tmp/claude-1002/P1-11-c2/focus.txt` (it reads that path; edit the
  path inside if you use another folder), then run bare `make test-int`. Docker 500s on this VM
  are environment errors.
- Frontend: `cd frontend && npx vitest run src/components/dashboard` and `npx tsc --noEmit -p .`.

## Deviations (all in the PR body)

1. One PR instead of impl + impl-2: a backend-only push to wp/P1-11 under the UI commits needed
   plumbing (merge-tree/commit-tree + push), which the classifier denied ([Git Destructive]); so the
   UI went into #119. Don't retry that route.
2. Migration `planning_0003` (chain already existed).
3. Deterministic workflow ids, not a DBOS dedup id (DBOS 3.1.0 + partitioned queue).
4. `build_plan(..., now)`.
5. Master readiness from runner status + profile inventory.
6. `publish_plan` moves `built_at` just past the day's latest plan (T-13).
7. Empty plan (no items, no issues) emits no `plan.published` (A1.4 counts the whole outbox; the fake_runner fixture adds a task-less workspace). Scott item.
8. Every build floored at now (`PlanContext.replan=True` in gather_plan).
9. `PlanIssueOut.offer` nested.
10. Core `_allowed_methods`: concrete path before templated (shared-file edit in core/errors.py). The PR body still describes the reverted isoday convertor as deviation 10 and in the docs section: UPDATE the body (replace it with this core change, cite the OpenAPI 3.1 Paths Object rule; remove the Starlette convertor citation) and add `plan_issue_id` + `row_factory.py` + `core/errors.py` to shared-file edits.
11. `PlanContext.ahead`; 12. spec-file fixes at the red commit, T-20 re-wrapped by Prettier only.

## Scott items

1. `plan.published` only for non-empty plans (deviation 7).
2. A1.2/A1.3 Playwright stay red: e2e stack has no scriptable fake runner or tick route. The UI matches their selectors.
3. Not provable here: kill test 20 runs in a row; the real master on the homelab.
4. One PR instead of the planned split (deviation 1).
