# P4-06 handoff (continuation 1 → 2)

Work package: P4-06 "README, docs and the v1 release". **PR #159**, https://github.com/SpaceshipCreative/tumnis-guide/pull/159 (ready for review, not draft). Branch `wp/P4-06`; push with `/usr/bin/git push origin HEAD:wp/P4-06`. Scratch dir `/tmp/claude-1002/P4-06-c1/` (the next one uses `-c2`). NEVER merge, tag or publish a release.

## State at handoff (head e819c86d, plus this handoff commit)

- Everything is built: harness, readme.yml, release verify-assets, README, OPERATIONS, INSTALL-CHECKLIST, RELEASE-CHECKLIST, CHANGELOG, .env.example (with #160's hosted variables), compose (proxy, TUMNIS_BACKUPS).
- **Every P4-06 marker is off.**
  - T-01..04 and T-07 were removed by c0 and confirmed passing in CI runs 36960239036 and 36961611213.
  - T-05, T-06 and T-08 were removed in 2e7a9212, citing the XPASS(strict) runs 36960239036 and 36961611213.
- **A4.4 `readme-install` is green** on every run since 5b2144c0. The latest is run 36969552162 on e819c86d.
- **CodeRabbit:** 7 comments, all fixed and resolved:
  - In bb82108d: 4162838308, 4162838311, 4162838313 and 4162838319.
  - In bce6f057: 4162915511 and 4162915517.
  - In e819c86d: 4163033457 (README `--wait` wording).
  - CodeRabbit's incremental review of e819c86d had not been checked when the handoff came.
- **CI on e819c86d** (ci run 36969552081):
  - integration-a and everything else passed.
  - integration-b was still in progress at handoff.
  - readme run 36969552162: success. version-skew run 36969552145: success.
  - `preview` stays pending (expected).
- origin/main was merged at 2ac75e65 and includes #160 (hosted keys) and #152 (stuck review).
- The PR body (`/tmp/claude-1002/P4-06-c1/pr-body.md`, also on the PR) needs its "Test results" CI ids updated to the final head. It also needs the 7th CodeRabbit comment (4163033457, fixed in e819c86d) and #160's hosted-key variables. The decision 75 Scott item is now resolved by #160.

## Remaining steps

1. Branch setup per the coordinator, then `/usr/bin/git merge origin/main`.
2. Check integration-b of ci run 36969552081, or of the run for the newest head: `gh pr checks 159`. If e2e fails only on A0.2 or the board keyboard drag, that is a known flake (it passed on the next run); re-run once.
3. Check that CodeRabbit finished on the head commit with no new thread:
   - `gh api repos/SpaceshipCreative/tumnis-guide/commits/<sha>/statuses` should show "Review completed".
   - The GraphQL `reviewThreads` query should show no unresolved thread.
   - Don't request a new review unless it never started (quota).
4. Update the PR body: `gh pr edit 159 --body-file <file>`, with the final run ids, the 7 comments and the hosted keys done.
5. `SendMessage` to "main": `#159 MERGE-READY at <sha>`.
6. Delete HANDOFF.md in a chore commit before the final report. That commit triggers CI again, so wait for green and report MERGE-READY at that sha.

## TODO(coordinator) markers still in README.md (resolve as each merges)

- #154 P3-14 impl-2: existing-folder mode.
- #156 P3-02, #157 P3-12 Obsidian and #158 P3-13 S3 source: Connect sources.
- FIX-app-findings: GitHub and Coolify Settings screens, and `/search`.
- #155 P4-04 and P3-09: "Your day" or "Agents".

The FIX-hosted-keys TODOs are resolved: #160 merged, and its variables are now in .env.example and the README.

## Deviations (for the PR body; accepted by the coordinator as defaults)

1. Caddy HTTPS proxy in compose (profile `standalone`).
2. `TUMNIS_BACKUPS` switch (unset means on; `.env.example` sets off). The backup entrypoint treats off, false, no and 0, in any case, as off.
3. `PUBLIC_BASE_URL` passed through.
4. Secret files owned by uid 10001, mode 0400.
5. curl smoke plus a headless first-run block in place of Playwright for A4.4.
6. Health accepts `backups: degraded`.
7. v1.0.0 kept under [Unreleased].
8. Local image build.
9. No `tumnis setup` CLI.

## Scott items

- Jev key has no Settings UI. The README documents `TYPESAFE_API_KEY` in each profile's `.env`.
- SFTP is documented only as a Settings > Storage location.
- GitHub, Coolify, planning, focus and triage settings are API only.
- The production restore is not scripted or rehearsed; only the drill, into a scratch volume, is.
- Release checklist rows 3, 9 and 13 stay pending.
- Discord is named as the first chat provider, with ADR-0014 to follow.

## Verify

```bash
cd backend && uv run pytest -q -p no:cacheprovider tests/meta/test_docs.py tests/meta/test_readme_test.py tests/meta/test_release_checklist.py tests/meta/test_release_assets.py
python3 scripts/readme_test.py --list
bash /tmp/claude-1002/P4-06-c1/check.sh   # make check with semgrep paths under the scratch dir; result in check.log
```

Helper scripts are in `/tmp/claude-1002/P4-06-c1/`: `wait_job.sh <run> <job> [min]`, `wait_cr_sha.sh <pr> <sha> [min]`, `reply.sh <comment id> <thread id> <body>`. Fetching job logs needs `allowed_domains: ["*.blob.core.windows.net"]` and `gh api --allow-escape-sequences`.
