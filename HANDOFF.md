# P0-24 handoff 6: PR #57 review loop (CI blocked by GitHub billing)

PR: https://github.com/SpaceshipCreative/tumnis-guide/pull/57 (`wp/P0-24` -> main). Do not merge.
Resume in a fresh worktree:

1. `/usr/bin/git fetch origin`
2. `/usr/bin/git switch -c agent/P0-24-c2 origin/wp/P0-24`
3. If HEAD didn't move (read-only `.git/config`) and `git diff --cached <origin sha>` is empty, run `/usr/bin/git symbolic-ref HEAD refs/heads/agent/P0-24-c2`.
4. Push with `/usr/bin/git push origin agent/P0-24-c2:wp/P0-24` (never force).

Use `/usr/bin/git`; plain `git` goes through rtk and the worktree guard rejects it.

## Blocker: GitHub Actions billing

Every CI job on `add3da8`, `3530ba9` and `9105c6f` failed with no steps run. The annotation reads "The job was not started because recent account payments have failed or your spending limit needs to be increased". Scott has to fix billing. After that, re-run CI: push a new commit, or `gh run rerun <id>`.

## Commits this round (after fc9ff0b, handoff 5)

- `0ad773d` ci(frontend): Vitest `maxWorkers: Math.max(2, availableParallelism() - 1)`.
  - Unit on fc9ff0b was cancelled at 3:06 against its 3:00 budget, with every step green. The budget is locked by T-P0-03-16.
  - The cause: Vitest's default is one worker on the 2-vCPU runner. With two workers, CI unit on 0ad773d was green in 2:57 (Vitest 60 s against 71 s). The whole run on 0ad773d was green, including e2e with the tabpanel wrapper and integration (6:24).
- `6a2e956` test(tasks) (red), then `a466baf` fix(tasks): `task_changes.task_version`, and `undo_task` requires it to equal the submitted version (CodeRabbit outside-diff Major). Migration `0003_task_changes` is edited, since it's unreleased.
- `add3da8` Merge origin/main (#47, P1-09). `3530ba9` Merge origin/main (#58, P1-03). Both merged cleanly, with no drift after `make gen` and still one `tasks` alembic head.
- `d6ba70d` test(frontend) (red), then `9105c6f` fix(frontend): `commentsQuery` follows `next_cursor` (CodeRabbit outside-diff Minor).
- The commit that adds this file.

## CodeRabbit

- The 6 inline threads from review 5358441815 have replies with their SHAs and are resolved. CodeRabbit confirmed each fix.
- Outside-diff (review 5358748131, `undo_task` version) is fixed in a466baf. Explained in a PR comment.
- Outside-diff (review 5359282827, `CommentList` paging) is fixed in 9105c6f. Explained in a PR comment, which also asks `@coderabbitai review`.
- Next agent: read any review after 5359282827 (`gh api repos/SpaceshipCreative/tumnis-guide/pulls/57/reviews`), including its outside-diff section. The last review said the PR had "used all 10 included reviews", so more may not arrive.

## Not yet verified by CI (because of billing)

- Integration: `test_undo_of_an_older_change_at_the_current_version_is_stale`, and T-P0-24-12 with the `task_version` column.
- Vitest: `CommentPages.test.tsx` passed locally. Unit job timing with 70 Vitest tests after the P1-09 merge.
- Everything else after 0ad773d.

## Local results this round

- `make check`: ruff, mypy, import-linter (9 kept), ESLint and tsc are green, and 977 backend unit tests pass.
  - Vitest was green (69/69) on 2 CPUs at 17:49 with `taskset -c 0,1`.
  - Later runs at load average 40-80 (other agents) timed out in long journeys: T-P0-24-11, T-P0-24-16, P0-02 render and P0-17 ProjectList. Each is a Testing Library `findBy` 1 s timeout or the 20 s test timeout. The P0-02 render test passes alone.
  - Rerun `npx vitest --run` in `frontend/` when load is low.
- Set `SEMGREP_SETTINGS_FILE`, `SEMGREP_LOG_FILE` and `SEMGREP_VERSION_CACHE_PATH` to `/tmp/claude-1002/...` for `make check`: `~/.semgrep` is read-only.

## Next steps

1. Once billing is fixed, get CI green on the head (`gh pr checks 57`). If `unit` or `integration` is cancelled at its budget with every test green, run `gh run rerun <id> --failed` once and note it.
2. Handle any new CodeRabbit comments.
3. Delete this file in a final `chore:` commit and push.

## Deviations (all in the PR body)

- Earlier rounds: see the PR body.
- This round:
  - Vitest `maxWorkers` at least 2.
  - The `task_changes.task_version` column.
  - `commentsQuery` reads every page instead of offering Load more.

## For Scott

- GitHub Actions billing or spending limit (blocker).
- The unit job margin is thin (2:57 of 3:00). The options are `pool: 'vmThreads'` or a bigger unit budget (T-P0-03-16 locks it).
- `preview` stays pending with no homelab runner.
