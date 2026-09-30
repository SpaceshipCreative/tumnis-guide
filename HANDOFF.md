# P0-29 handoff (continuation c2)

Written on the coordinator's "STOP NOW" message (the VM ran out of RAM). No local stacks, tests or servers were left running.

- PR: **#88** https://github.com/SpaceshipCreative/tumnis-guide/pull/88
- Branch: `wp/P0-29`
- The PR body is up to date as of 7891040 (`$TMPDIR/P0-29-c2/body.md`).

## Commits this continuation (after c1's 75935d0; main 57306ee is merged in)

| SHA | What |
| --- | --- |
| 9bb0b79 | merge origin/wp/P0-29 into main (includes the OpenSSL fix, #90) |
| e33b1fc | decision 26: the LAN Lighthouse gate. `lighthouserc.json` uses rtt 50, 10240 Kbps and 2x CPU, and LCP, CLS and TBT are all `error`. New `lighthouserc.slow-phone.json` (Lighthouse default throttling, all `warn`, 1 run). Decision 25: the performance job's homelab `runs-on` gets a fork guard. |
| 48c0d96 | **SPEC CHANGE** `test(perf): decision 27 regression rule (20% and at least 50 ms)`: T-P0-29-06 plus the plan text (DETAILED plan, IMPLEMENTATION-PLAN.md, work-packages.yaml) |
| 6b99eb9 | `limitMs = min(NFR, max(round(1.2b), round(b+50)))`, with comments to match |
| 1e911b0 | GitHub-hosted runs skip the frontend install and Chromium; the LHCI package is prefetched during the stack build |
| 24dc7ea | TEMPORARY A0.6 proof run in e2e (reverted in f95ef42) |
| f95ef42 | revert of 24dc7ea |
| 9f6989a | the slow-phone warning run covers the dashboard only |
| 632b828 | `compose up --build` (CodeRabbit fix) |
| 7891040 | `perf/baseline.json` re-taken from 12 CI runs (values unchanged, commit 632b828) |
| (this) | HANDOFF.md |

## State

- **CI on 7891040:** every job green (lint, unit, contract, integration, daemon, e2e, skills, security, performance, spec-guard, red-proof, traceability, version-skew). `preview` is pending (expected), and GitGuardian was pending.
  - performance took 4:38 on this run. The slow-phone step took 51 s because the runner CPU was contended (BenchmarkIndex 1000); it is normally 18 to 22 s.
  - Earlier runs: 4:42, 4:22, 4:08, 3:57.
- **A0.6 evidence:** run 36716668468 on 24dc7ea, e2e job; all timing tests passed.

  | Test | Timings (ms) |
  | --- | --- |
  | A0.6 dashboard ready | 648 |
  | A0.6 quick-add ready | 302 |
  | A0.6 review ready | 231 |
  | T-01 | 644 / 603 / 619 |
  | T-02 | 268 / 243 / 300 |
  | T-03 | 151 / 158 / 146 |

- **Threads:** both CodeRabbit threads from this continuation are resolved. The `--build` thread was fixed (632b828). On the HOMELAB_RUNNER thread I declined, and CodeRabbit withdrew the finding.
- **Messages:** "#88 needs labels" was sent to main (`spec-change` for decision 27; `perf-baseline`).
- **CodeRabbit:** I commented `@coderabbitai review` once, around 13:04Z. **Review 5366808209 on 7891040 arrived just as the STOP came, and I have NOT read it.**

## Next steps

1. Read review 5366808209:
   - `gh api repos/SpaceshipCreative/tumnis-guide/pulls/88/reviews/5366808209 -q .body`
   - its inline comments, filtered on `pull_request_review_id`
   - unresolved threads (GraphQL `reviewThreads`)

   Fix or decline each one and resolve it. Don't request another full review unless needed (the quota is tight).
2. Delete this HANDOFF.md in a commit, then push with `/usr/bin/git push origin HEAD:wp/P0-29`.
3. When CI is green (preview pending is fine) and there are 0 unresolved threads, send "#88 MERGE-READY at <sha>" to main.
4. Optional: if the performance job goes over 5:00 in CI, trim k6 (quick-add 25 s, dashboard 15 s). The 50 ms floor makes the extra p95 noise from fewer samples irrelevant.

## Scott items (in the PR body)

1. The LAN gate uses a 2x CPU multiplier (high-end phone, per Lighthouse's BenchmarkIndex table; runner 1,944 to 2,974). At 4x, TBT is 161 to 525 ms and fails the gate.
2. PRD PERF-2 and the ARCHITECTURE CI table still say "20%". Should they say "20% and at least 50 ms"?
3. Operator: register the homelab runner, set HOMELAB_RUNNER=true, and re-baseline there.
