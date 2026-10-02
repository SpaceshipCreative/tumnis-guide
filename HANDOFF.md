# P4-06 handoff (continuation 2 → 3)

Work package: P4-06 "README, docs and the v1 release". **PR #159**, https://github.com/SpaceshipCreative/tumnis-guide/pull/159 (ready for review, not draft). Branch `wp/P4-06`; push with `/usr/bin/git push origin HEAD:wp/P4-06`. Scratch dir `/tmp/claude-1002/P4-06-c2/` (the next one uses `-c3`). NEVER merge, tag or publish a release.

## State at handoff

- This handoff commit sits on top of a merge of origin/main at 7cfd670b (#166 docling CI). `make check` was green on that merge, and there are no doc changes for #166.
- **CI on the previous head 7395d7c7** (main at 5af90889): every job green after one performance re-run.
  - ci run 36994268646. `performance` first failed on the quick_add p95 (363 ms against a 218 ms limit) and passed on the re-run.
  - readme run 36994268617: `readme-install` green.
  - version-skew run 36994268529: green.
  - CI on the handoff commit has not run yet.
- **CodeRabbit:** 0 unresolved threads. The last review, of d13ee547, completed with no new comment. Auto reviews are paused on this PR, so `@coderabbitai review` triggers an incremental review. Request one only when new P4-06 content lands.
- **Every TODO(coordinator) marker is gone** from the repo and from the PR body.
- **Docs now describe these merges:**
  - #154: existing folders and the folder move.
  - #160 and #167: hosted keys in `deploy/hosted-keys.env`, the Compose 2.24.0 floor, `hosted_url_requires_https`, and removing the file before rolling back past 3f678e79.
  - #156: Settings > Connections, with no provider yet.
  - #162: the Search page.
  - #155: unattended runs (Settings > Unattended runs, Run unattended, Close the day's queued list).
  - #161: capture, Just added and the drawer's History.
  - #172: `TumnisConnectorSyncStale` ignores `pending_auth` connections.
- **release.yml** creates a draft release, verifies the assets, then publishes (b2c8c3aa).

## Commits this continuation (c2)

| Commit | Change |
| --- | --- |
| b2acb642 | README: #154 existing folders; TODO markers rewritten |
| 632e80e5 | old HANDOFF.md removed |
| b2c8c3aa | release draft, verify, then publish |
| 4ecbb5ee | composer drop uploads |
| d8039d8a | #167 hosted keys and #156 Connections |
| 16da5e7b | CodeRabbit round of 7 comments |
| 6a03fc4c | pepper tokens, bind address, mcp-stdio https |
| c6d30c3e | #162 Search page |
| 4702d89d | #155 unattended runs |
| d98718d1 | contract migrations, export TUMNIS_PASSWORD, #161 |
| d13ee547 | cert renewal script, TumnisConnectorSyncStale |
| 7395d7c7 and the #166 merge | merges of main |

## Remaining steps

1. Branch setup, then `/usr/bin/git merge origin/main`. Check what merged for user- or operator-facing changes, and document them in README, OPERATIONS or CHANGELOG.
2. `bash <scratch>/check.sh` (copy `/tmp/claude-1002/P4-06-c2/check.sh` with the paths changed), then push.
3. Wait for CI on the new head: `gh pr checks 159`.
   - Known flakes, each allowed ONE re-run: T-P0-24-05, A1.1 [laptop], performance TBT or quick_add p95, T-P0-07-05, test_label_latency.
4. Delete HANDOFF.md in a chore commit and push. Wait for CI green on that sha.
5. Update the PR body: `python3 /tmp/claude-1002/P4-06-c2/body3.py "ci run <id>"` edits `/tmp/claude-1002/P4-06-c2/pr-body.md`.
   - Fix the head sha and run ids in its Test results text (the script hardcodes 7395d7c7 and the readme/version-skew ids).
   - Then `gh pr edit 159 --body-file /tmp/claude-1002/P4-06-c2/pr-body.md`. Run gh from the worktree, not from /tmp.
6. SendMessage to "main": `#159 MERGE-READY at <sha>`.

## Deviations (accepted by the coordinator; in the PR body)

1. Caddy proxy (`standalone` profile).
2. `TUMNIS_BACKUPS` switch.
3. `PUBLIC_BASE_URL`.
4. Secret files uid 10001, mode 0400.
5. curl smoke plus a headless first run in place of Playwright for A4.4.
6. Health accepts `backups: degraded`.
7. v1.0.0 kept under [Unreleased].
8. Local image build.
9. No `tumnis setup` CLI.
10. Docker Compose 2.24.0 floor. docs.docker.com dates `env_file` `required` to 2.20.0; the docs follow deploy/README.md (#167).

## Scott items

- Jev key has no Settings UI (`TYPESAFE_API_KEY` in each profile's `.env`).
- GitHub and Coolify settings are API only.
- The production restore is not scripted.
- Release checklist rows 3, 9 and 13 stay pending.
- Discord is the first chat provider (ADR-0014 to follow).
- `tumnis mcp-stdio` sends the key over LAN `http://` (CWE-319, P2-01 code). The coordinator logged it, and the README recommends https.
- `DeleteAtSourceDialog` (#154) is not mounted anywhere.
- Out-of-scope CodeRabbit items on knowledge/move.py:
  - stream the copy;
  - check the 50 MiB size before a move.
  - Both are logged as main follow-ups; #170 fixed the third.

## Verify

```bash
cd backend && uv run pytest -q -p no:cacheprovider tests/meta/test_docs.py tests/meta/test_readme_test.py tests/meta/test_release_checklist.py tests/meta/test_release_assets.py tests/meta/test_release_workflow.py tests/meta/test_compose.py
python3 scripts/readme_test.py --list
```

Helper scripts are in `/tmp/claude-1002/P4-06-c2/`:

- `wait_run.sh <run> [min]`
- `wait_cr.sh <sha> [min]`
- `threads.sh` and `threads_full.sh`: unresolved threads.
- `/tmp/claude-1002/P4-06-c1/reply.sh <comment id> <thread id> <body>`

Job logs need `allowed_domains: ["*.blob.core.windows.net"]`. Write git and gh helper scripts with the Write tool; the worktree guard refuses complex inline commands that mention git.
