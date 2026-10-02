# HANDOFF: P3-13 (S3 buckets as a source), after continuation c3

Binding instructions: `~/tumnis-coordinator/prompts/wave1/P3-13.txt` (plus the files it lists).

Branch setup: from your throwaway branch run `/usr/bin/git merge origin/main`, then
`/usr/bin/git merge origin/wp/P3-13`. Push with `/usr/bin/git push origin HEAD:wp/P3-13`.

PR: **#158**, READY (not a draft), https://github.com/SpaceshipCreative/tumnis-guide/pull/158.
- CodeRabbit was requested once (07:09Z) and has re-reviewed by itself on each push.
  Its hourly quota is exhausted, so don't comment `@coderabbitai review` again.
- The PR body is current. Scratch copy: `$TMPDIR/P3-13-c3/pr-body.md`.

## State at 39157378 (CI all green, `preview` pending as expected)

- integration-b passed on its re-run of run 36979923301.
  - The first attempt failed only on P3-02's flaky `test_connect_oauth.py::test_oauth_flow_completes_through_callback`.
  - That test asserts `b'c1' not in` a dump that holds random UUIDs. Reported to main; not our test.
- Merged main at ea0706b3 (#156 P3-02).
  - knowledge_0011 has `down_revision = "knowledge_0009"`.
  - P3-12 (#157, knowledge_0010) is still open. Re-chain only when the coordinator says so.
- S3 connections use `integrations.upsert_connection` (status ok), like calendar and github, not P3-02's `create_connection`.
  - Reasons: there is no OAuth for S3, and the framework only sees registered providers.
  - `create_s3_source(ctx, s, body, ...)` now takes ctx. PR-body deviation 4 explains this.
- Decision 89 is done: `_outside_file_target` answers 409 `linked_source_read_only`.

## Commits this continuation (all on origin/wp/P3-13)

| SHA | What |
| --- | --- |
| `efd98880` | semgrep no-fstring SQL fix; row_factory `FILLED_COLUMNS` (`folder_files.location_id`) |
| `9c7b2595` | `test(knowledge)`: remove the 9 P3-13 markers (XPASS in CI run 36964982002) |
| `37913b79` | live-map NOT_LIVE for the S3 reads |
| `33cae5d8` | merge main 3152b76 (#154); knowledge_0011 down 0009; row_factory `s3_sources.provider` |
| `df1d48bb` | decision 89 test (red in CI run 36971608149) |
| `51951067` | decision 89 fix |
| `44420add` | chore: handoff notes while waiting on P3-02 |
| `a3989848` | merge main ea0706b3 (#156); upsert_connection swap |
| `383b1bcf` | chore: removed HANDOFF.md |
| `2e6c1ee0` | MinIO key check: bucket-level writes and deletes count (CodeRabbit #1) |
| `39157378` | extraction requested per version; recheck catches PathRejected (CodeRabbit #2, #3) |

## CodeRabbit status

First review (383b1bcf): 3 threads. All fixed, replied to and resolved.

Second review (39157378): **2 new findings, both NOT yet addressed.**

1. **Inline thread 4163813943, `rules.py` `_on_any` (OPEN).** The probe-key resource match misses sub-prefix grants.
   - Example: `s3:DeleteObject` on `arn:aws:s3:::BUCKET/acme/sub/*`, with `acme/` mapped, is accepted as read-only. Valid finding.
   - Planned fix: replace the probe-key match with an overlap test per Resource pattern.
     - No wildcard: overlap iff `pattern == bucket_arn` or `pattern.startswith(f"{bucket_arn}/{prefix}")`.
     - Wildcard: `fixed = text before the first * or ?`; overlap iff `fixed.startswith(base)` or `base.startswith(fixed)`, where `base = f"{bucket_arn}/{prefix}"`.
     - NotResource: overlaps (cautious; the scoping check already refuses it).
   - Then derive write and delete from the overlapping statements' S3 action patterns:
     - only-reads patterns are ignored;
     - only-deletes patterns count as delete;
     - anything else counts as write;
     - `_deletes(p)` also counts as delete.
   - That replaces the `granted("s3:PutObject"/"s3:DeleteObject", objects)` calls. Keep `can_read` and `can_list` as they are.
   - Write the unit test first, in `tests/unit/test_s3_capability_rules.py`. Cover `s3:DeleteObject` and `s3:PutObject` on `BUCKET/acme/sub/*` with `acme/` mapped.
   - These existing tests must stay green:
     - `test_minio_put_on_another_prefix_does_not_count` (`uploads/*` with only `acme/` mapped);
     - `test_minio_read_only_policy_is_read_only_and_scoped`;
     - T-P3-13-01 and T-P3-13-03.
2. **Outside-diff finding (review body only, no thread), `s3_sync.py` `read_linked`.** When the spool copy is lost, `read_linked` reads the current object.
   - `pipeline.read` then calls `store.set_content`, which overwrites that version's `content_hash` with the new bytes' digest. Valid finding.
   - Planned fix: `read_linked` looks up the version's stored `content_hash`, hashes while it yields, and at the end raises a new `LinkedObjectChanged(FileNotFoundError)` if the digest differs. `_copy` then propagates it before `set_content`, so nothing is rewritten. The next sync takes in the new version.
   - Integration test in `tests/integration/test_s3_source.py`:
     1. `s3_sync.configure(SELF_HOSTED)`, and reset it afterwards (`configure(None)`).
     2. Sync with `ExtractLog`, take the version id from `log.requests[0][1]`, then `pipeline.spool_path(version_id).unlink(missing_ok=True)`.
     3. Put new bytes on the same key.
     4. Collecting `s3_sync.read_linked(ws.ctx, version_id)` raises `LinkedObjectChanged`.
     5. The version's `content_hash` is unchanged.
   - Reply to this finding in a PR comment, since it has no thread.

## Remaining steps

1. Fix finding 1, then finding 2 (test first; separate `fix(knowledge)` commits).
2. Run `make check` with semgrep state under scratch: `bash $TMPDIR/P3-13-c3/check.sh`, or copy it to your own folder and edit the paths.
3. Push, reply on thread 4163813943 and resolve it, and comment on finding 2.
4. Wait for CI with a background loop capped at 30 minutes (`wait_ci2.sh` pattern).
   - If integration-b fails only on the P3-02 OAuth flake, re-run it once.
   - If performance fails only on the board TBT, re-run it once.
5. When CI is green and no threads are open (CodeRabbit may auto-review again; handle any new findings), send main "#158 MERGE-READY at <sha>".
6. If P3-12 merges first (the coordinator will say), merge main, re-chain knowledge_0011 onto knowledge_0010 and fix its docstring line, then run `make gen` and `make check`.
7. Delete HANDOFF.md in a chore commit at the very end.

## Deviations / Scott items

These are all in the PR body (11 deviations).
- MinIO's bearer token stands in for a signature (plan deviation 8). Needs Scott's acknowledgement.
- The capability check runs in the api process at connect.
- Linked connections are knowledge-owned (upsert_connection, own 15-minute tick).
- New `"linked"` pipeline source: no download of the original file.
- The webhook uses auth `none` plus a token checked through a SECURITY DEFINER function.
- `POST` is not idempotent.
- New "Linked sources" Settings section.
- Objects over 50 MiB are skipped.
- Decision 89 (coordinator default).
- The B2 recordings were written from Backblaze's public docs.

## Verify

```bash
cd backend && uv run pytest -q -p no:randomly -m "not integration and not contract" tumnis/modules/knowledge/tests/unit/test_s3_capability_rules.py tumnis/modules/knowledge/tests/unit/test_s3_source_rules.py
gh pr checks 158
gh api graphql -f query='query { repository(owner:"SpaceshipCreative", name:"tumnis-guide") { pullRequest(number:158) { reviewThreads(first:50) { nodes { id isResolved } } } } }'
```
