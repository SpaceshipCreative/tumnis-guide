# HANDOFF: P3-13 (S3 buckets as a source), after continuation c3

Binding instructions: `~/tumnis-coordinator/prompts/wave1/P3-13.txt` (plus the files it lists).
Branch: push with `/usr/bin/git push origin HEAD:wp/P3-13` from your throwaway branch after
`/usr/bin/git merge origin/main` and `/usr/bin/git merge origin/wp/P3-13`.
PR: **#158** (DRAFT) https://github.com/SpaceshipCreative/tumnis-guide/pull/158. The body is
complete (`gh pr view 158`); CodeRabbit NOT requested yet. Keep it a draft until P3-02 (#156)
merges (coordinator, binding).

## State at 51951067 (all CI jobs green apart from `preview`, which is expected)

- All 9 spec markers are off (CI run 36964982002 showed XPASS).
- The c3 fixes:
  - semgrep: no f-string SQL in knowledge_0011;
  - `row_factory.FILLED_COLUMNS` fills `folder_files.location_id`, and `COLUMN_VALUES` gives `s3_sources.provider`;
  - `live-map.ts` NOT_LIVE has the two S3 reads.
- origin/main merged at 3152b76 (#154). knowledge_0011 now has `down_revision = "knowledge_0009"`.
- Decision 89 (coordinator default): `_outside_file_target` in knowledge/api.py answers 409
  `linked_source_read_only` for linked docs. The new test
  `test_linked_document_is_never_deleted_at_source` was red in CI run 36971608149 and is green now.

## Next steps

1. When the coordinator says P3-02 (#156) merged:
   - merge origin/main;
   - swap `s3_sources._new_connection` (it uses `integrations.seed_connection`) to P3-02's
     `create_connection(ctx, provider, settings, *, account_label, ...)`, which needs a registered provider spec for `s3`.
     Check its signature on main;
   - consider P3-02's connector tick vs `knowledge-s3-source-tick`;
   - `make gen`, then `make check`, push, and wait for CI.
2. When P3-12 (#157, knowledge_0010) merges: merge main, then re-chain knowledge_0011 onto knowledge_0010.
   Also update the docstring line "chained after knowledge_0009".
3. Then: `gh pr ready 158`; `gh pr comment 158 --body "@coderabbitai review"` (once); run the review loop.
   When it's green with no threads, send "#158 MERGE-READY at <sha>" to main.
4. Delete HANDOFF.md in a chore commit at the very end.

The deviations and Scott items are in the PR body (11 deviations; decision 89 is number 10).
Scratch: `$TMPDIR/P3-13-c3/` (pr-body.md, check.sh).
