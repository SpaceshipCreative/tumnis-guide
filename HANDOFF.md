# HANDOFF: FIX-app-findings (APP-01..13), continuation c2

Binding: `~/tumnis-coordinator/prompts/wave1/FIX-app-findings.txt`, `~/tumnis-coordinator/vm-agent-rules.md`, `~/tumnis-coordinator/scott-decisions.md`.

- PR: https://github.com/SpaceshipCreative/tumnis-guide/pull/162 (ready for review, branch `fix/app-findings-1`). Do NOT merge.
- PR body source: `/tmp/claude-1002/FIX-app-findings-c1/pr-body.md` (current). Apply it with `gh pr edit 162 --body-file <file>`.
- Push with `/usr/bin/git push origin HEAD:fix/app-findings-1`. Use only `/usr/bin/git`. Never force-push, switch, reset or stash.
- Scratch: `/tmp/claude-1002/FIX-app-findings-c1/` (semgrep settings, PR body, wait scripts).

## Commits since the first handoff (c1)

| SHA | What |
| --- | --- |
| 6cc3c490 | chore: drop the first handoff (after merging main 3f678e7) |
| ddaedf65 | Search: an outside `?q=` change replaces the box; a failed later page keeps results + Try again (CodeRabbit) |
| 9d07f89d | Drawer: if the project's recurrence list fails, ask for the task's rule (CodeRabbit) |
| d22f465f + its test commit | **Reset 409 root cause**: `agents/api.py` `seed_agent` adopts the provision row and cancels `provision:<project id>` (coordinator-approved deviation) |
| 5e3ed31a | Drawer: a loaded list still answers after a failed refresh (CodeRabbit) |
| ad0c8382 | Merge origin/main (ea0706b: #167 compose-skew, #156 P3-02) |
| c506a250, 5ceb20f8 | Seed adopts only when no other profile holds the name (CodeRabbit) |
| (this commit) | HANDOFF.md |

## State at handoff

- CI on ad0c8382: **all green**, including e2e, integration-a/b, version-skew and performance. `preview` stays pending forever, so don't wait on it. 5ceb20f8 and this commit still need CI.
- CodeRabbit: every thread is replied to and resolved, except possibly PRRT_kwDOUx-vCc6oQWhw (agents adoption name check). `/tmp/claude-1002/FIX-app-findings-c1/replies3.sh` replies to it and resolves it; check whether it ran (`isResolved`).
- Decision on the "gate the form" thread: declined, because the locked T-P0-24-16 needs the Repeats form as soon as the drawer opens. The coordinator agreed.

## Remaining steps

1. Delete HANDOFF.md (`/usr/bin/git rm HANDOFF.md`, `chore: drop FIX-app-findings handoff`), run `make check` with the semgrep env (below), and push.
2. If replies3.sh did not run, run it now.
3. Wait for CI on the new head with a hard deadline: `bash /tmp/claude-1002/FIX-app-findings-c1/wait-ci.sh`. Don't wait on `preview`. Re-run a failing job at most once. One earlier e2e flake (A0.2 plus a board keyboard drag on d22f465) went green on the next run.
4. Wait for CodeRabbit's incremental review of 5ceb20f8 (`gh api repos/SpaceshipCreative/tumnis-guide/pulls/162/reviews`). Fix or reply to any new thread, then resolve it.
5. When CI is green and no threads are open, SendMessage to `main`: `#162 MERGE-READY at <sha>`.
6. Final report: the PR URL; each APP id; CI per job; CodeRabbit outcome; Scott items.

## Findings (PR body has file:line)

- Fixed: APP-01, APP-02, APP-04 (deadlock retries + seed race), APP-08, APP-09, APP-11, APP-12.
- Not reproducible: APP-03 (#138 T-SEED-29).
- Scott items: APP-10 (day plan 404: a nullable 200 or a has-plan flag), APP-13 (no plan-specified Settings screens for GitHub, Coolify, planning, focus or triage; the plan's sections all exist, connections came with P3-02). APP-09 follow-up: `recurrence_rule_id` on TaskOut, so older instances show their rule.

## Verify

- `cd backend && uv run pytest -q -p no:randomly tumnis/modules/agents/tests/integration/test_seed_agent_race.py` (needs Docker; the sandbox blocks the socket, so run it unsandboxed)
- `cd frontend && npx vitest run src/routes/search.test.tsx src/components/project/drawer`
- `SEMGREP_SETTINGS_FILE=/tmp/claude-1002/FIX-app-findings-c1/semgrep-settings.yml SEMGREP_LOG_FILE=/tmp/claude-1002/FIX-app-findings-c1/semgrep.log SEMGREP_VERSION_CACHE_PATH=/tmp/claude-1002/FIX-app-findings-c1/semgrep-version make check`
