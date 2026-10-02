# HANDOFF: P3-13 (S3 buckets as a source), after continuation c2

Binding instructions: `~/tumnis-coordinator/prompts/wave1/P3-13.txt` (plus the files it lists).
Branch: push with `/usr/bin/git push origin HEAD:wp/P3-13` from your throwaway branch after
`/usr/bin/git merge origin/main` and `/usr/bin/git merge origin/wp/P3-13`.
PR: **#158** (DRAFT) https://github.com/SpaceshipCreative/tumnis-guide/pull/158. CodeRabbit NOT
requested yet: request it once, when the PR is marked ready (`gh pr ready 158`, then
`gh pr comment 158 --body "@coderabbitai review"`). No review threads yet.
P3-02 (#156) is still a DRAFT, not merged: final wiring waits for the coordinator's "P3-02
merged" (then swap `s3_sources._new_connection` to P3-02's `create_connection`).

## Commits (on origin/wp/P3-13)

- `test(knowledge): P3-13 spec tests (red)`: T-P3-13-01..09, strict xfail.
- `10599116` rules, `knowledge_0011` migration, models (WIP).
- `36d76056` merge of origin/main (67a43da, #153 P3-14: knowledge_0008) into the branch;
  `knowledge_0011` now has `down_revision = "knowledge_0008"` (main's knowledge head).
- `6761a657` adapters: `adapters/port.py` gains `S3SourceReader`, `KeyCapabilityCheck`;
  `adapters/s3_source/{connector,capability,fake}.py`; registered `knowledge.s3_source` and
  `knowledge.key_capabilities`; contract classes (`test_b2_capability.py`: fake + recorded;
  `test_s3_source_contract.py`: fake + real MinIO, whole file marked integration).
- `cfec454b` implementation: `knowledge/s3_sources.py` (DTOs, create/list/get/delete,
  `accept_minio_notification`, `open_source`, test seam `use_s3_adapters`), re-exported in
  `api.py`; `knowledge/s3_sync.py` (`sync_source`, `recheck_key`, `read_linked`,
  `sources`); `pipeline.py` Source gains `"linked"` (spool, never placed; lost spool re-read
  through `register_linked_reader`) and `spool_path()`; `workflows.py`
  `knowledge_s3_source_sync`, `knowledge_s3_source_recheck`, schedule
  `knowledge-s3-source-tick` (15 min, sync queue); router: `GET/POST /knowledge/s3-sources`,
  `GET/DELETE /knowledge/s3-sources/{connection_id}`, `POST /webhooks/minio/{connection_id}`
  (policy `MINIO_WEBHOOK`: auth none, csrf False, 1 MiB); `tests/meta/test_security_definer.py`
  ALLOWED gains `app.s3_source_webhook`; `make gen` output (openapi.json, frontend/src/api);
  frontend `components/knowledge/S3SourceSetup.tsx` (+ `.test.tsx`, 2 passing) mounted as a new
  Settings section `sources` ("Linked sources") in `settings/sections.ts` and
  `routes/settings.$section.tsx` (NOT inside StorageSection: duplicate labels would break the
  locked StorageSection test).
- `bfb4fa7c` squawk: the `location_id DROP NOT NULL` uses `-- squawk-ignore
  ban-drop-not-null` with the reason (squawk now passes).

## State at handoff

- Local: ruff, ruff format, mypy, lint-imports clean; backend unit layer 1880 passed, the only
  failures are the 17 T-01/T-04 XPASS(strict) cases plus the semgrep meta test (needs
  SEMGREP_* env vars under $TMPDIR, known VM issue). B2 contract test XPASS(strict) locally.
  Frontend typecheck, eslint and the new Vitest file pass. Integration NOT run locally.
- CI: run for `bfb4fa7c` just started; nothing read yet. `gh pr checks 158`.

## Next steps

1. Read CI for bfb4fa7c (`gh pr checks 158`, `gh run view <id> --log-failed`). Expect XPASS(strict)
   for T-01, T-02, T-04 (unit/contract) and, if the implementation is right, T-03, T-05..T-09
   in integration-a (knowledge). Fix real failures. Likely risk spots:
   - T-03: `KeyCapabilityChecker.check_minio` signs `GET <endpoint>/minio/admin/v3/accountinfo`
     with botocore `S3SigV4Auth` and sends it through `guarded_client` (Host must match the
     signed host:port). The earlier probe used minio-py's MinioAdmin (same API). If MinIO
     answers 403 for a non-admin user, the plan's fallback is "unchecked with warning".
   - T-06/T-07: these use `extract_env` (TUMNIS_ADAPTERS=fake); S3 sources deliberately use the
     REAL connector in every mode (seam `use_s3_adapters`), so they read the MinIO container.
     Extraction goes through `api.enqueue_extract(ctx, version_id, "linked")`.
   - T-08: expects exactly 3 finished recheck workflows and an empty spool.
   - `test_adapter_registry` (unit) passes; `test_table_registry` / `test_security_definer`
     (Docker) unverified locally.
2. When CI shows a test XPASS(strict), remove its marker in one commit citing the run id
   (`test(knowledge): remove P3-13 spec markers (XPASS in CI run <id>)`), never touching an
   assertion (decision 78; retry once if denied, then report READY EXCEPT MARKERS).
3. `make check` (with SEMGREP_SETTINGS_FILE/LOG_FILE/VERSION_CACHE_PATH under $TMPDIR) after
   markers are off; push; PR body (template below); `gh pr ready 158`; request CodeRabbit once;
   run the review loop; MERGE-READY message to main when green.
4. When P3-02 merges: merge origin/main, swap `_new_connection` to `create_connection` (check
   its signature on main), re-chain `knowledge_0011` to main's knowledge head if P3-12/#154
   merged first (coordinator: #154 = knowledge_0009, P3-12 = knowledge_0010), `make gen`.
5. Delete HANDOFF.md in a chore commit at the very end.

## Deviations / Scott items (for the PR body)

1. MinIO "signature" is the bearer `auth_token` (plan deviation 8, kept); constant-time
   compare of SHA-256s; unknown connection = same 401.
2. The capability check runs from the api process at connect (plan: "refused at connect"),
   outside AGENTS.md's storage-port carve-out: needs Scott's OK, same terms as P1-14's.
3. Linked source tables are knowledge-owned (`s3_sources`, `folder_files.connection_id`); the
   connection row comes from `integrations.seed_connection` until P3-02's `create_connection`;
   scheduling is a new knowledge `knowledge-s3-source-tick` until P3-02's connector tick.
4. Webhook route auth "none" + token check (no `webhook_token` AuthMode exists), mirroring
   github's WEBHOOK policy; needs SECURITY DEFINER `app.s3_source_webhook(uuid)` (allow-listed).
5. Linked objects are extracted with a new pipeline source `"linked"` (spooled, scanned,
   extracted, never copied into a project folder; FR-15.8 says linked items are read-only and
   open in their source). So Tumnis cannot serve the original file of an S3 document (no
   download); flag for Scott (serve from S3 later vs re-host).
6. S3 sources use the real connector/checker in fakes mode too (a linked bucket has no
   Tumnis-side fake world); tests swap fakes via `use_s3_adapters`.
7. `POST /knowledge/s3-sources` is `idempotent=False` (the token is shown once and never stored,
   so the idempotency cache must not hold it).
8. New Settings section "Linked sources" (`sources`) for S3SourceSetup.
9. Objects over 50 MiB are skipped before download (logged), not turned into failed docs.
10. B2 recordings written from Backblaze's public docs (no live account), values invented.
11. Docs consulted: Context7 `/boto/botocore` (SigV4Auth.add_auth, headers_to_sign incl. host),
    Context7 `/minio/minio-py` (MinioAdmin account_info, policy_add/user_add/policy_set),
    https://www.backblaze.com/apidocs/b2-authorize-account,
    https://docs.min.io/aistor/administration/bucket-notifications/publish-events-to-webhook/,
    minio/minio `internal/event/target/webhook.go` (Bearer prefix; Records[].s3.object.key
    QueryEscape'd).
12. Migration `knowledge_0011` (down knowledge_0008), tell the coordinator.

## Verify

```bash
cd backend && uv run pytest -q -p no:randomly tumnis/modules/knowledge/tests/unit/test_s3_source_rules.py tumnis/modules/knowledge/tests/unit/test_s3_capability_rules.py
cd backend && uv run pytest -q -m contract tumnis/modules/knowledge/tests/contract/test_b2_capability.py
cd backend && uv run pytest -q tumnis/modules/knowledge/tests/contract/test_s3_source_contract.py -k Fake
cd backend && uv run python ../scripts/ci/squawk_migrations.py --changed-since origin/main
cd frontend && npx vitest run src/components/knowledge
```

Scratch: `$TMPDIR/P3-13-c2/` (edit scripts only).
