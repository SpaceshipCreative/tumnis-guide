# P4-02 handoff (Stuck handling, FR-10.5), continuation c1 to c2

PR #145 (https://github.com/SpaceshipCreative/tumnis-guide/pull/145), branch `wp/P4-02`. The `spec-change` label is set (by the coordinator).

## State at handoff
- HEAD is 958a0ad3 plus this handoff commit. 958a0ad3 merges origin/main, bringing #144 (P2-16, a996b0e).
  - `test_coverage.py` SKILLS keeps ("master","focus"), ("master","relay") and ("project-template","stuck").
  - The `hostile/index.yaml` conflict was resolved by regenerating it (`harness packets`, then `harness index --suite hostile`).
  - `make gen` changed nothing.
  - `make check` and the skills-job harness step both pass locally.
- `alembic heads` (before this merge): one head per module, agents_0009 (P4-02), focus_0002, decisions_0004. #139 (P2-06) also claims agents_0009 but hasn't merged.
- All 6 T-P4-02 integration markers and StuckPanel's test.fails are removed. Every spec test passes: T-P4-02-01..07 integration, 04 unit, 09 Vitest. T-P4-02-08 skill cases stay strict xfail (homelab only).
- CodeRabbit: all 3 threads fixed and resolved, plus the outside-diff FocusBar note. No open threads.
- CI: on 713b046e (merge of main incl. #148's integration-a/-b split), a run was started; a background wait was running. 958a0ad3 has not been through CI yet. The push of this handoff starts a new run.

## Remaining steps
1. Wait for CI on the pushed HEAD with one background wait (`gh pr checks 145`). `preview` stays pending (no homelab runner). Integration is now two jobs (integration-a, integration-b).
2. A cancelled integration job may be re-run once (`gh run rerun <id> --failed`); never edit ci.yml.
3. When everything else is green, delete this HANDOFF.md in a `chore:` commit and push. That needs one more CI round.
4. Send "#145 MERGE-READY at <sha>" to main with SendMessage.

## Decisions applied
- 61: SKILLS line (commit bc4b6cc6).
- 63: `_stuck.call_tool` reports a tool error's own status; T-P4-02-02 changed only its import line (feada164).
- 64: T-P4-02-02's fixed sleep became a `wait_until` poll (1ebd0b33). Accepted by the coordinator.

## Key commits (c1)
- 17fb4ff4: StuckPanel.
- 98251c46: skill, cases, packets, hostile base; template 1.2.0.
- 4a7df17e: stuck.resolved contract fixture.
- ce19ade2: stuck run keeps the task's status.
- 72275eff: TEMPLATE_VERSION 1.2.0.
- e49559bd and ad5de6e4: marker removals.
- 49cf6d3b: a later Stuck waits on the active stuck run (test_stuck_rejoin.py).
- d25c303c: earlier handoff removal.
- 713b046e and 958a0ad3: main merges.

## Deviations
All are listed in the PR body:
- run priority 10 for normal and enrich runs;
- outcome delivered through the stuck.resolved subscriber;
- a stuck run moves no task status;
- per-check-in dedupe key;
- no add_comment tool;
- handle_stuck's extra args;
- decisions 63 and 64;
- no A4.2 test on main.

## Scott items
- What accepting or rejecting a stuck run's `result` review item should do.
- The homelab live try.

## Verify
- `bash $TMPDIR/P4-02-c1/check.sh` (make check with semgrep paths in scratch).
- `bash $TMPDIR/P4-02-c1/skills.sh` (the CI skills step).
- Stuck integration files locally (Docker; outside the sandbox through the permission gate, which the coordinator OK'd): `cd backend && uv run pytest -q -n 0 -m integration tumnis/modules/agents/tests/integration/test_stuck.py tumnis/modules/agents/tests/integration/test_stuck_rejoin.py`.
- Use `/usr/bin/git` and push with `/usr/bin/git push origin HEAD:wp/P4-02`.
