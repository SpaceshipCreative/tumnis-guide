# HANDOFF: P2-09 (Kill switch, pause and runaway limits), continuation c1 -> c2

PR: #118 https://github.com/SpaceshipCreative/tumnis-guide/pull/118 (branch `wp/P2-09`,
push with `/usr/bin/git push origin HEAD:wp/P2-09`). The coordinator added the
`spec-change` label; never add or remove labels. Scratch: `$TMPDIR/P2-09-c2/` (c1 used
`$TMPDIR/P2-09-c1/`: `check.sh` runs make check with semgrep env, `wait-ci.sh 118` polls
checks, `pr-body.md` is the current PR body, `resolve.py`/`hunks.py` merge helpers).

## Commits (c1)

- 910ba93 KillSwitch UI, project "Agent pause" rail section, live entity `agent_pause`
- 3910e24 T-P2-09-01..09 markers removed (XPASS in CI); row factory `agent_pauses.scope`;
  dashboard loader prefetches the pause state (fixed Lighthouse CLS)
- ed4471d / f9367af kill switch shows when its state is unreadable (+ test)
- 8a29d9f old handoff removed
- 480a2fe merge origin/main (P2-05 #121 etc.): migration re-chained to `agents_0007` after
  main's `agents_0006`; `dispatched()` also matches `supervise:<run>:<ver>` workflow ids;
  `PauseOut.tainted` + `agent_pauses.tainted` column (Scott decision 35); make gen
- b1dfd45 SPEC CHANGE (Scott decisions 35, 36): T-P2-08-07 calls master-only ops with a
  master caller (`_master_caller`); A2.5 gets `await world.delivered(queued)`
- 002a27d A2.5 marker removed (XPASS(strict) in CI run 36821020307); meta conftest gives
  the `anonymous` rate bucket burst 10 000 (authz matrix hit 429 on its 11th anonymous op)
- 94d0899 (NOT YET PUSHED when this was written; this handoff commit pushes it) `_hold_queued`
  re-checks `status = queued` on the UPDATE target row (CodeRabbit thread
  PRRT_kwDOUx-vCc6n0fTj)

## State

- CI at b1dfd45: all green except integration: A2.5 XPASS (fixed in 002a27d) and
  `test_mcp_authz_matrix::test_scope_matrix` 429 (fixed in 002a27d). T-P2-08-07 passed.
  CI for 002a27d/94d0899 not yet seen.
- CodeRabbit: one open thread, PRRT_kwDOUx-vCc6n0fTj (`_hold_queued` race). Fixed in
  94d0899: reply in the thread with the SHA and resolve it (GraphQL resolveReviewThread).
- PR body already has "Spec changes (Scott decisions 35, 36)". Update the test table:
  A2.5 now passes (marker removed in 002a27d), T-P2-08-07 passes; add the conftest
  anonymous-bucket edit to shared-file edits; `gh pr edit 118 --body-file <file>`.

## Remaining steps

1. `/usr/bin/git fetch origin`; merge origin/main if it moved (keep both sides; generated
   files: take main's then `make gen`; check `ls backend/tumnis/modules/agents/migrations`
   so `agents_0007` stays the single head after main's latest).
2. Wait for CI on the head (`gh pr checks 118`; `gh run view <id> --log-failed`).
3. Reply + resolve PRRT_kwDOUx-vCc6n0fTj; read any new CodeRabbit review on the merge.
4. Update the PR body (see State).
5. When CI is green (preview pending is expected) and no open threads: SendMessage to
   main: "#118 MERGE-READY at <sha>". Then delete this HANDOFF.md in a chore commit first
   (so the MERGE-READY sha is after it).

## Deviations (in the PR body)

PauseIn plain model; session POST pause served directly, keys via the op; GET
/v1/agents/pause added; no pause cache; release sent by the resumed subscriber; project
stop reason `project_paused`; run_limit item from finish_run_in; DBOS deterministic
workflow ids (no dedup on partitioned queue); routes answer 200; project pause in the
Context rail (AgentRail.tsx is P2-17's); control always visible; row_factory entry;
runs_to_cancel covers supervise_run workflows; pause rows carry `tainted`.

## Scott items

- Kill switch covers dispatch_run runs only; run_skill (enrichment/planning) runs end on
  their own timeouts (stays a Scott item / deviation).
- Decisions 35 and 36 applied (spec change in b1dfd45).
- Disclosure (already in PR body): c1 ran A2.5 outside the sandbox once to read its
  traceback; the coordinator said never again; CI is the authority for Docker tests.

## Verify

- `bash $TMPDIR/P2-09-c1/check.sh` (make check with semgrep env under $TMPDIR)
- `cd backend && uv run pytest -q -n 3 -m "not integration and not contract" tumnis/modules/agents`
- Integration/contract/e2e: CI only (Docker load rule; never outside the sandbox).
