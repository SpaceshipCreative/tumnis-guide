# HANDOFF: FIX-main-followups-2 (PR #174)

Task: `~/tumnis-coordinator/prompts/wave1/FIX-main-followups-2.txt`. One PR, review loop until clean; never merge.

- PR: https://github.com/SpaceshipCreative/tumnis-guide/pull/174 (ready for review, base main, head `fix/main-followups-2`)
- Worktree: `.claude/worktrees/agent-a09a8b829ac4a488a`, local branch `worktree-agent-a09a8b829ac4a488a` (read-only .git/config: `git switch -c` failed). Push with `/usr/bin/git push origin HEAD:fix/main-followups-2`.
- Scratch: `/tmp/claude-1002/FIX-main-followups-2/` (pr-body.md, commit.sh, review.sh, reply.sh, comments.sh, wait_ci.sh).

## Findings (all done)

| # | Finding | Commit(s) |
| --- | --- | --- |
| 1 | `finish_provision_step` guarded on the start status; `superseded` when taken over | 46a0aec2 (+ 4ff2a98e pepper fixture for its test) |
| 1b | CodeRabbit on #174: `send_provision_step` sends nothing for a taken-over profile | f62b3f51 |
| 1c | CodeRabbit on #174 (outside diff, workflows.py:1399): `choose_name_step` locks the profile row (`with_for_update`) so it can't put an adopted profile back to `provisioning` | handoff commit (see below) |
| 2 | Seed adopts only provision-written statuses (`provisioning`, `not_provisioned`) | 5716e4b1, 6ed3c593 |
| 3+4 | plan_issue: unavailable Accept/Edit hidden (sr-only reason), Edit payload-free (plan line 10125) | 7e203ebb |
| 5 | RecurrencePicker stale cached rule | 9afb4732, 922787db (+ handoff commit test tweak) |
| 6 | SearchPage draft kept across its own URL write | d6df28cd |
| 7 | move.py: `too_large` precheck before copying (no streaming; reason in PR body) | 9e361c20 |

Deviation (in PR body): finding 2 also adopts `not_provisioned` because the seed writes projects before runners (`tumnis/seed.py`), so the provision can end `no_runner` first; refusing it would bring back APP-04.

## Review threads

- 4165034854 (knowledge/workflows.py:481, DBOS step order): declined (DBOS default app version = workflow-source hash, so no cross-version replay). Replied, resolved.
- 4165034862 (RecurrencePicker.tsx:237, arrival time): replied (save's invalidation uses cancelRefetch), test added in 922787db. Resolved.
- 4165132589 (RecurrencePicker.stale.test.tsx:151, thread `PRRT_kwDOUx-vCc6oUCog`): **OPEN**. Handoff commit changes that test to assert the old snapshot answered after the save (`answeredAfterSave`), and the saved repeat still shows. Reply with that, and add: if a stale result did reach the cache after the save, the UI assertion fails (list newer than rule → "Does not repeat"). Then resolve it.
- Outside-diff, review on 2d3c3b4e (send_provision_step): fixed in f62b3f51, noted in a PR comment.
- Outside-diff, review on 922787db (choose_name_step at workflows.py:1399-1401): fixed in the handoff commit with test `test_provision_adopted.py::test_choosing_the_profile_waits_for_an_adoption_in_progress`. Post a PR comment naming the commit (it has no thread).

## CI

On 922787db everything passed except `unit: cancel` (cancelled, not failed: re-run it or push again) and `preview: pending` (not required). After pushing the handoff commit, watch `gh pr checks 174`. Known flakes, one re-run allowed: T-P0-24-05, A1.1 [laptop], J1 A1.2 (fix/j1-flake), performance TBT, T-P0-07-05.

Local `make test-int` was unusable (Docker 500s under VM load); rely on CI for integration.

## Remaining steps

1. Push this commit, then reply to and resolve thread `PRRT_kwDOUx-vCc6oUCog` (`bash /tmp/claude-1002/FIX-main-followups-2/reply.sh 4165132589 PRRT_kwDOUx-vCc6oUCog <body file>`).
2. PR comment for the choose_name_step outside-diff fix. Add one line to the PR body under finding 1 (`gh pr edit 174 --body-file /tmp/claude-1002/FIX-main-followups-2/pr-body.md` after editing it).
3. Wait for CI and for any CodeRabbit incremental review (don't request a full review; the quota is spent). Handle new threads.
4. Merge origin/main only if GitHub shows #174 out of date or conflicting.
5. When CI is green and there are 0 open threads, send main "#174 MERGE-READY at <sha>".

## Verify

- `make check` (set SEMGREP_SETTINGS_FILE, SEMGREP_LOG_FILE and SEMGREP_VERSION_CACHE_PATH to files in the scratch dir)
- `npx vitest run src/components/review src/components/project/drawer src/routes/search` from `frontend/`
- `bash /tmp/claude-1002/FIX-main-followups-2/review.sh` (reviews, inline comments, thread states)

## Scott items

None. Plan-issue Edit is answered by plan line 10125.
