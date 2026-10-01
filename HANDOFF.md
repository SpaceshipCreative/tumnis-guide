# HANDOFF: SEED (phase 1 and 2 acceptance seed), continuation 0

PR **#129** `[SEED] impl: phase 1 and 2 acceptance seed`, branch `wp/SEED`. Push with `/usr/bin/git push origin HEAD:wp/SEED`. Scratch files are in `$TMPDIR/SEED-c0/` (`/tmp/claude-1002/SEED-c0/`): `inventory.md`, `pr-body.md`, and the `watch_ci.sh`, `wait_cr.sh` and `wait_jobs.sh` helpers.

## Commits on wp/SEED

| SHA | What |
| --- | --- |
| 52aa22f | `test(seed)`: T-SEED-01..09 (unit), T-SEED-10 (integration), T-SEED-11..12 (Playwright), red |
| 62a7405 | `feat(seed)`: the acceptance set (`backend/fixtures/acceptance/*.yaml`); loader sections `runners`, `agents`, task `links`, `acceptance_criteria`; seed writers in agents (`seed_runner`, `seed_agent`) and integrations (`seed_link`); acceptance `seed` and `fake_runner` fixtures; recordings `enrich__hybrid_invoice` and `plan__write_proposal_block`; T-SEED-01..09 green |
| c782be7 | `test(core)`: T-SEED-13 reset at an anchor, red |
| 9c7cbcc | `feat(core)`: `POST /v1/test/reset?anchor=`; `fixtures.ts` `seedSetFor(tags)` and the acceptance reset; T-SEED-10..13 markers removed |
| a8ab23a | merge origin/main (#120 merged) |
| 2d19cd7 | `test(acceptance)`: A1.5 asks for `kind=enrich` (Scott decision 41, the coordinator adds the spec-change label) |
| 57d03ba | `test(acceptance)`: A1.4 step 3 marker removed (CI showed XPASS(strict)) |

## CI on 2d19cd7 (run 36823928082)

- **Green:** unit, contract, lint, security, e2e (T-SEED-11/12 pass, no journey flipped), traceability, red-proof, daemon, skills, GitGuardian, version-skew, CodeRabbit.
- **spec-guard:** fails as expected on the A1.5 one-line edit until the coordinator adds `spec-change`.
- **integration:** 1 failed, 1467 passed. The failure was the A1.4 step 3 XPASS(strict), and 57d03ba removes that marker. A1.4 step 2 (`spec:P1-08`) is still xfail. T-SEED-10 and T-SEED-13 passed.
- **performance:** total-blocking-time only (Lighthouse, budget 200 ms). The job was re-run once (`gh run rerun 36823928082 --job 110245277469`), and the re-run result still needs checking. Don't touch budgets.
- **preview:** pending (no homelab runner); that's expected.

57d03ba is committed and pushed together with this file. Its CI hasn't been read yet.

## CodeRabbit

`@coderabbitai review` was requested once, when the PR opened. Its summary comment is posted; no review with inline comments had arrived when this file was written. Next: read the reviews, inline comments and unresolved threads (pr-review-loop.md steps 2 to 4). Fix each point or reply with a reason, then resolve the thread. A background waiter (`wait_cr.sh 129 1`) was running.

## Remaining steps

1. Read CI on the pushed head. If integration is green and performance passes (or fails only on TBT after the one re-run, which you report), go on to step 2.
2. Work the CodeRabbit threads. Push. Comment `@coderabbitai review` only if needed (the quota is tight).
3. Update the PR body:
   - A1.4 step 3 is flipped green. A1.4 step 2 stays red under P1-08: its offline/online agents are the conftest's seed runner, and the step needs a check of why it still fails. Read its failure in the integration log, `sed` on `/tmp/claude-1002/SEED-c0/int.log`, around `test_one_project_agent_offline`.
   - The e2e journeys stayed red. Since #120 merged, check whether A2.1 (J3) and A2.2 UI are still red only for UI or timing reasons.
4. When CI is green and no threads are open, send `#129 MERGE-READY at <sha>` to main.
5. Follow-up `wp/SEED-impl-2` (not started): phase 1 FakeAgent playback in compose.
   - `dispatch_step`/`run_skill` would answer from the `runner` fake script `<profile>/<skill>` (#120's `Phase1Script`), loading `tests/fakes/recordings/runner/<result>` (they are in the image; `.dockerignore` keeps tests) and resolving `title:` picks and the sentinel task id as `tests/fakes/fake_runner.py` does. It would `DBOS.send` the result on `api.run_topic`.
   - Fakes-mode availability: `agent_for_project` and `master_agent` would count a profile whose runner never heartbeated as ready while `fake_scripts.enabled()`.
   - `POST /v1/test/fakes/runner/offline`.
   - `planner-tick` and `calendar-sync` test ticks on #127's `core/ticks.py` once #127 merges.
   - A1.3's `calendar.google` `no_ninety_minute_gap` scenario.

## Decisions and deviations (also in the PR body)

- The base `seed` set stays as it is. The coordinator approved the new `SeedSet.acceptance`: `seed/workspace.yaml` plus `fixtures/acceptance/`. The e2e journeys get it by `@A1.x`/`@A2.x` tags, reset with `anchor=2026-03-09`.
- The backend acceptance `seed` is `ACCEPTANCE_WORLD`: the seed set plus the acceptance projects and briefs, with no agents. The acceptance `fake_runner` makes the runner and the agents in the seed workspace and marks the seed's outbox rows sent.
- `Fix footer link` is in the backlog, not Today, because of A1.6's Rolls over. A third project, `Gamma ops`, holds the agent `gamma-ops`. The acceptance calendar has Monday busy 09-10, 12-13 and 15-15:30, and Tue/Wed busy 09-10.
- No secrets: runner tokens and agent keys are generated at load time.

## Scott items

- Review the acceptance-set shape (decision 37 said "within the existing seed files") and the `Fix footer link` status.
- Who mints a profile key in production (carried from #120).

## Verify

```bash
cd backend && uv run pytest -q tests/harness -m "not integration and not contract"
SEMGREP_SETTINGS_FILE=$TMPDIR/SEED-c0/semgrep-settings.yml SEMGREP_LOG_FILE=$TMPDIR/SEED-c0/semgrep.log SEMGREP_VERSION_CACHE_PATH=$TMPDIR/SEED-c0/semgrep-version make check
gh pr checks 129
```
