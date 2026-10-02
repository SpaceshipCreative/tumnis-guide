# HANDOFF: FIX-app-findings (APP-01..13)

Binding instructions: `~/tumnis-coordinator/prompts/wave1/FIX-app-findings.txt` and `~/tumnis-coordinator/agent-reports/restart-note-gen7.txt`.

- PR: https://github.com/SpaceshipCreative/tumnis-guide/pull/162 (draft, branch `fix/app-findings-1`). Do NOT merge.
- PR body source: `/tmp/claude-1002/FIX-app-c1/pr-body.md`. It is current. Apply it with `gh pr edit 162 --body-file <file>`.
- Worktree: HEAD is on the local branch `worktree-agent-afd992e979a5f33db` because the `.git/config` lock stopped `switch -c`. Push with `/usr/bin/git push origin HEAD:refs/heads/fix/app-findings-1`. Always use `/usr/bin/git`. Never force-push, stash, reset or clean.

## Commits (all on fix/app-findings-1)

| SHA | Finding |
| --- | --- |
| 3552f8d0 | APP-01 Activity view `initialPageParam` |
| 68e65d31 | APP-08 Tiptap `injectCSS: false`, base CSS in styles.css |
| fc4a1805 | APP-11 `useLiveSocket` in AppShell (no /ws before sign-in) |
| 1260f538 | APP-12 `plan_issue` review slot |
| 478978d9 | APP-04 reset deadlock retries (10, full-jitter pause) |
| 4812ab74 | APP-02 Search page |
| 10f72b0e | APP-09 drawer asks for a task's rule only when it repeats |
| (this commit) | HANDOFF.md. Delete it in a later commit before ready, so it never lands on main. |

`make check` passed locally (exit 0, 147 Vitest files / 354 tests) before 4812ab74 and 10f72b0e.

## State

- CI on #162: the run for 10f72b0e had just started, with all jobs pending. On the earlier SHA, everything except e2e and integration-a/b was green, and those were still pending. Docker tests run in CI only. Re-run a cancelled job at most once. Never wait on `preview`.
- CodeRabbit: not reviewed yet, because the PR is a draft. There are no review threads.
- Findings: APP-03 is not reproducible (#138, T-SEED-29). APP-10 and APP-13 are Scott items. The details are in the PR body.

## Remaining steps

1. `/usr/bin/git rm HANDOFF.md`, commit (`chore: drop FIX-app-findings handoff`), push.
2. (done) PR body updated from the file above; re-apply after any change.
3. Wait for CI with a hard deadline, using `gh pr checks 162`. Fix any red job TDD-first.
4. `gh pr ready 162`, then post one comment, `@coderabbitai review`.
5. Review loop: fix or reply to each thread, then resolve it. Run `make check` before each commit, with the semgrep env inline: `SEMGREP_SETTINGS_FILE=/tmp/claude-1002/FIX-app-c1/semgrep-settings.yml SEMGREP_LOG_FILE=/tmp/claude-1002/FIX-app-c1/semgrep.log SEMGREP_VERSION_CACHE_PATH=/tmp/claude-1002/FIX-app-c1/semgrep-version make check`.
6. SendMessage to `main`: `#162 MERGE-READY at <sha>`.
7. Final report: PR URL; each APP id (fixed file:line / not reproducible / Scott item); CI per job; CodeRabbit outcome; Scott items.

## Decisions and deviations

- APP-08: fixed in the component (`injectCSS: false`), not the CSP. A headless round-trip test was dropped because it could not go red (the headless editor never mounts). The defensive flag stays in `markdown.ts`.
- APP-09: the drawer matches on `latest_task_id`, so an older instance of a rule shows "Does not repeat". The proper fix is `TaskOut.recurrence_rule_id`, which is for Scott.
- APP-12: Edit on a plan issue still opens the generic text form. This is a follow-up.
- APP-02: Placeholder.tsx is deleted because nothing else imported it.

## Verify

- `cd frontend && npx vitest run src/routes/search.test.tsx src/components/project src/components/review src/editor src/components/common`
- `cd backend && uv run pytest tumnis/core/tests/unit/test_reset_deadlock_attempts.py`
