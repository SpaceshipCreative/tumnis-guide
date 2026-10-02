# HANDOFF: P3-13 (S3 buckets as a source)

Binding instructions: `~/tumnis-coordinator/prompts/wave1/P3-13.txt` (plus the files it lists).
Branch: pushed as `wp/P3-13` from the throwaway branch (`/usr/bin/git push origin HEAD:wp/P3-13`;
a stale local `wp/P3-13` exists in a prunable worktree, ignore it).
PR: **#158** (DRAFT) https://github.com/SpaceshipCreative/tumnis-guide/pull/158. CodeRabbit NOT
requested yet: request it once, when the PR is marked ready (coordinator rule).
CI: the red commit's run had not been read at handoff time; check `gh pr checks 158`.

## Commits

- `test(knowledge): P3-13 spec tests (red)`: T-P3-13-01..09, all strict xfail (unit verified XFAIL;
  contract verified XFAIL; integration collected, red by construction, CI confirms).
- `feat(knowledge): P3-13 rules, s3_sources migration and model (WIP)`: the pure rules, extra
  unit tests, migration `knowledge_0011`, models. T-01 and T-04 now pass locally (they show as
  XPASS(strict) failures until the markers go; `make check` fails on exactly those 17 cases).

## Marker rule (decision 78)

Remove a marker only after CI shows the test passing (XPASS(strict)), cite the CI run id in the
commit (`test(knowledge): remove P3-13 spec markers (XPASS in CI run <id>)`), never change an
assertion. So: implement everything, push with markers on, read the CI run, then remove them.

## Design (settled with the advisor; follow it)

- Follow P3-12's shape (origin/wp/P3-12 `_vault.py`): the connection row comes from
  `integrations.api.seed_connection(s, "knowledge", "s3", <account>)` behind ONE seam function
  in knowledge api (`_new_connection`), to be swapped for P3-02's `create_connection` once P3-02
  merges (P3-02 was still a handoff with no implementation; coordinator: wait for "P3-02 merged"
  before final wiring). Account string: `s3:<new uuid>` (unique per workspace+provider+account).
- Persistence: knowledge-owned `s3_sources` table (migration `knowledge_0011`, down
  `knowledge_0007` for now: the coordinator reserved 0008-0010 for P3-14/P3-12 and asked for
  0011 "down knowledge_0010"; a down of 0010 would break the chain on this branch, so it points
  at main's head and re-chains at merge; tell the coordinator). `folder_files` gains
  `connection_id` (FK NOT VALID), `location_id` becomes nullable, a NOT VALID check that exactly
  one is set, partial unique index (workspace_id, connection_id, path). SECURITY DEFINER
  `app.s3_source_webhook(uuid)` returns (workspace_id, webhook_token_sha256): ADD it to
  `backend/tests/meta/test_security_definer.py::ALLOWED` with a reason. Check the table registry
  (`backend/tests/meta/test_table_registry.py`) passes for `s3_sources` (made by
  create_tenant_table). Run squawk after committing:
  `cd backend && uv run python ../scripts/ci/squawk_migrations.py --changed-since origin/main`.
- Rules (DONE, in `knowledge/rules.py` after `etag_equal`, kept out of the file's end to avoid
  conflicts with P3-12/P3-14): `KeyCapabilities`, `UNCHECKED`, `UNVERIFIED_KEY`,
  `capabilities_acceptable`, `b2_key_capabilities(allowed, bucket)`,
  `minio_key_capabilities(policy, bucket, prefixes)`, `looks_like_b2_master_key_id`,
  `normalize_prefix`, `prefix_for`, `FolderFileLite`, `S3ObjectLite`, `s3_change`.
- Adapters (TODO): protocols `S3SourceReader` (list(prefix, cursor) -> Page[FileStat], stat(key),
  read(key), health(), aclose()) and `KeyCapabilityCheck` (check_b2(key_id, application_key, *,
  bucket), check_minio(endpoint, region, access_key, secret_key, *, bucket, prefixes)) in
  `adapters/port.py`. `adapters/s3_source/connector.py`: `S3SourceConnector` composing
  `S3Storage(config, bucket=..., prefix="", conditional_put=False, net_policy=..., resolver=...)`
  and exposing only the read methods. `adapters/s3_source/capability.py`:
  `KeyCapabilityChecker(Adapter)` with `__init__(*, net, resolver=system_resolver, transport=None,
  clock=None)` (transport = the `inner` of `core.net.guarded_client`):
  - B2: `GET https://api.backblazeb2.com/b2api/v4/b2_authorize_account`, Basic
    base64(`keyId:applicationKey`), parse `apiInfo.storageApi.allowed` with
    `rules.b2_key_capabilities`. 401 -> AdapterRejected (api: 422 `key_rejected`).
  - MinIO: `GET <endpoint>/minio/admin/v3/accountinfo`, signed with botocore
    `S3SigV4Auth(Credentials(ak, sk), "s3", region)` on an `AWSRequest`, sent through
    `guarded_client`. VERIFIED empirically on the pinned chainguard/minio image: a non-admin user
    may call it and the answer is plain JSON `{"AccountName", "Policy": {...policy doc...},
    "Buckets": [...]}` (Policy may also be a JSON string on other versions: json.loads it). Parse
    with `rules.minio_key_capabilities`. A 4xx -> 422 `capability_check_failed` (tell the user to
    pick provider "other" to connect unchecked).
  - `adapters/s3_source/fake.py`: `FakeS3Source` (in-memory objects, put/remove helpers, calls)
    and `FakeKeyCapabilities` (scripted per key, default read-only scoped).
  - Register in `adapters/__init__.py`: `knowledge.s3_source` and `knowledge.key_capabilities`.
    The adapter meta-test (`tumnis/core/tests/unit/adapters/test_adapter_registry.py`) then needs
    contract classes: fake + recorded for key_capabilities (add to `test_b2_capability.py`,
    replaying the B2 recordings), fake + real (minio container) for s3_source
    (`tests/contract/test_s3_source_contract.py`).
- api (TODO, knowledge/api.py): `S3PrefixMap(prefix, project_id|None)`, `S3SourceIn(provider:
  "minio"|"b2"|"other", endpoint, region, bucket, access_key, secret_key, path_style, trusted,
  prefixes)`, `S3SourceOut` (id = connection id, provider, endpoint, bucket, prefixes, trusted,
  `capabilities: KeyCapabilities`, `warning: str|None`, version) and `S3SourceCreated(S3SourceOut)`
  adding `webhook_token` (shown once) plus the `mc admin config set ... notify_webhook:<id>
  endpoint=... auth_token=...` and `mc event add ALIAS/BUCKET arn:minio:sqs::<id>:webhook --event
  put,delete` commands. `create_s3_source(s, body, *, net, resolver=system_resolver)` order:
  bucket name check (`_s3_root`), prefixes normalized + `safe_prefix`, hosted needs https
  (`endpoint_needs_https`), SSRF `resolve_and_check` on the endpoint with `endpoint_policy`
  (422 `ssrf_blocked`, nothing sent; T-09), B2 master-key heuristic (422 `b2_master_key`), the
  capability check per provider, `capabilities_acceptable` (refused -> 422 `key_not_read_only`
  with the reason; T-03), then the connection (seam) and the `s3_sources` row (keys sealed with
  `settings_store.seal_for_workspace`, aad `s3_sources:<id>`; token =
  `secrets.token_urlsafe(32)`, stored as sha256). Also list/get/delete for the UI.
  Webhook accept: `accept_minio_notification(connection_id, authorization, body)` -> calls
  `app.s3_source_webhook` (owner-free, app role), requires `Authorization: Bearer <token>`
  (MinIO sends "Bearer " + token when the token is one word: minio/minio
  internal/event/target/webhook.go), `hmac.compare_digest` on sha256, else 401
  `invalid_token` (also for an unknown connection). Body: `Records[].s3.object.key`
  URL-unescaped with `urllib.parse.unquote_plus` (QueryEscape'd by MinIO), capped (e.g. 100
  records); the body's bucket is ignored. Enqueue one `knowledge_s3_source_recheck(workspace_id,
  connection_id, key)` per key via `deadletter.dbos_client().enqueue_async` on the `sync` queue
  (pattern: `api.enqueue_extract`). 202.
- Sync engine (TODO, new `knowledge/s3_sync.py`): `sync_source(ctx, connection_id, *, net,
  extract=None)` lists each mapped prefix through the reader, compares with `folder_files` rows
  of the connection via `rules.s3_change`, skips objects over `MAX_UPLOAD_BYTES` before
  download, downloads new/changed objects to `<spool>/<version_id>` (pipeline spool dir), creates
  or versions the Document (`connection_id`, `external_id` = key, `source` "s3", project from the
  prefix map, trust/taint = trusted source ? ("trusted", False) : ("untrusted", True)), upserts the
  folder_files row (origin external, content_hash sha256 hex), then calls `extract(workspace_id,
  version_id)` (default: `api.enqueue_extract(ctx, version_id, "spool")`). A key gone (delete
  marker on a versioned bucket) soft-deletes the Document (trash) and the row. `recheck_key(ctx,
  connection_id, key, *, net, extract=None)`: HEAD the key in the source's own bucket; only a key
  under a mapped prefix that exists and changed is ingested (T-08: nothing else happens).
  Extraction note: the pipeline's `place` step copies a spool upload into the project folder
  (`uploads/<name>`) and rewrites `documents.path`; keep `external_id` as the S3 key (tests read
  by `external_id`). Check `store.new_document` can take trust/taint/source/connection columns
  (add a small store helper if not).
- Workflows (TODO): `knowledge_s3_source_sync(workspace_id, connection_id)` and
  `knowledge_s3_source_recheck(workspace_id, connection_id, key)` on the `sync` queue; the
  existing 15-minute `knowledge-folder-sync-tick` also enqueues every source's sync (until
  P3-02's connector tick takes over; flag in PR). `s3_sync.configure(net)` from the worker like
  `sync.configure`.
- Router (TODO): session routes `GET/POST /knowledge/s3-sources`, `GET/DELETE
  /knowledge/s3-sources/{connection_id}`; webhook `POST /webhooks/minio/{connection_id}` on the
  (unprefixed) knowledge router with a policy like github's `WEBHOOK` (auth "none", csrf False +
  reason, idempotent False + reason, 1 MiB cap). The plan wanted auth `webhook_token`; no such
  AuthMode exists, so mirror github (deviation for the PR body). Then `make gen` (OpenAPI,
  client) and commit generated files.
- Frontend (TODO): `frontend/src/components/knowledge/S3SourceSetup.tsx` (endpoint, region,
  bucket, prefix map, path-style toggle, trust toggle; shows the warning and the token +
  mc commands once), DS-01 primitives, 375 px.

## Scott items / deviations for the PR body

1. MinIO "signature" is the bearer `auth_token` (plan deviation 8, kept).
2. The capability check runs from the api process at connect (plan: "refused at connect"),
   outside AGENTS.md's storage-port carve-out: needs Scott's OK, same terms as P1-14's.
3. Linked source tables are knowledge-owned (`s3_sources`, `folder_files.connection_id`); the
   connection row is created through `seed_connection` until P3-02's `create_connection` lands;
   scheduling rides knowledge's 15-min tick until P3-02's connector tick.
4. Webhook route auth "none" + token check (no `webhook_token` AuthMode exists).
5. B2 recordings were written from Backblaze's public docs (no live account), values invented.
6. Docs consulted: Context7 `/minio/minio-py` (MinioAdmin policy_add/user_add/policy_set,
   account_info), Context7 `/boto/botocore` (SigV4Auth), first-party
   https://www.backblaze.com/apidocs/b2-authorize-account,
   https://docs.min.io/aistor/administration/bucket-notifications/publish-events-to-webhook/ (no
   header format given) and minio/minio `internal/event/target/webhook.go` (Bearer prefix; body
   `event.Log{EventName, Key, Records}`, object key QueryEscape'd).

## Verify

```bash
cd backend && uv run pytest -q -p no:randomly tumnis/modules/knowledge/tests/unit/test_s3_source_rules.py tumnis/modules/knowledge/tests/unit/test_s3_capability_rules.py
cd backend && uv run pytest -q -m contract tumnis/modules/knowledge/tests/contract/test_b2_capability.py
make check   # semgrep meta test needs SEMGREP_SETTINGS_FILE/LOG_FILE/VERSION_CACHE_PATH under $TMPDIR
# Docker-backed: rely on CI (integration-a runs knowledge), or bare `make test-int` once.
```

Scratch: `$TMPDIR/P3-13-c0/` (probe.py: the MinIO accountinfo probe, run in a python:3.13-slim
container on the MinIO container's network with the venv's site-packages mounted).
