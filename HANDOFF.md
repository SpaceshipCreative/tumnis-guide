# P1-14 handoff: Storage interface, server path and S3

Worktree `/Users/sjordan/Projects/Tumnis-Guide-wt/p1-14`, branch `wp/P1-14` (from main at bcbae03 after merging P0-18). Nothing is pushed. Read the rules first: `/private/tmp/claude-501/-Users-sjordan-Projects-Tumnis-Guide/21cf8b82-5220-4984-97b2-7fb0d6482a35/scratchpad/prompt-footer.md`, `AGENTS.md`, `CLAUDE.md`, and the plan section `docs/IMPLEMENTATION-PLAN-DETAILED.md` lines 10458-10710 (P1-14).

## Commits so far

| SHA | What |
| --- | --- |
| a6ba60c | `test(knowledge): P1-14 spec tests (red)`: every spec test as a strict xfail, plus interface skeletons (storage port, adapter classes, api signatures raising NotImplementedError) |
| 314156c | `feat(knowledge): safe_rel_path, is_network_fs, etag_equal` (T-04, 05, 06 green); adds `unicodedata` to the rules allow-list |
| b0bb5c5 | `feat(knowledge): FakeStorage passes the storage contract` (T-01 green) |
| e909158 | `feat(knowledge): ServerPathStorage ...` (T-02, T-07 green) |

## Spec tests

Green (marker removed): T-P1-14-01, 02, 04, 05, 06, 07.

Still red (`@pytest.mark.xfail(strict=True, reason="spec:P1-14")` or Vitest `test.fails`):
- T-P1-14-03 `tests/contract/test_storage_s3.py::TestS3StorageMinio` (class marker)
- T-P1-14-11 `tests/contract/test_storage_s3.py::test_head_check_fallback_catches_concurrent_change`
- T-P1-14-10 `tests/learning/test_s3_conditional_writes.py::test_minio_behavior_matches_record`
- T-P1-14-08, 09, 12, 13, 14 `tests/integration/test_locations.py`
- T-P1-14-15 `frontend/src/components/settings/StorageSection.test.tsx` (`test.fails`)

(All paths above are under `backend/tumnis/modules/knowledge/` unless shown.)

## Remaining TDD steps, in order

1. **S3Storage** (`adapters/s3.py`; skeleton has the final signatures, `S3Config`, `ConditionalWriteProbe`). Remove the T-03 class marker, then T-11, then T-10.
   - aioboto3 client kept open (AsyncExitStack, entered lazily; `aclose()` exits it); creating a client per call is too slow for the 1,050-file listing test. `AioConfig(s3={"addressing_style": "path" if path_style else "auto"}, signature_version="s3v4", retries={"max_attempts": 1}, connect_timeout=5, read_timeout=30)`.
   - SSRF: when `net_policy` is given, check the endpoint with `tumnis.core.net.resolve_and_check` at client creation, and pass `connector_args={"resolver": GuardedResolver(...)}` (aiobotocore 2.25.1 accepts an `aiohttp.abc.AbstractResolver` there; verified in `aiobotocore/config.py`). The resolver calls `resolve_and_check(host, port, policy, resolver)` and returns the checked address, so every connect is pinned. aiohttp skips the resolver for IP literals, so the creation-time check covers those. Port policy for S3: `DEFAULT_PORTS | {9000}`, plus the endpoint's explicit port in self-hosted mode (decision; say so in the report).
   - Keys: `prefix + safe_rel_path(path)`; `list` uses `list_objects_v2` with `Prefix=prefix+safe_prefix(p)`, `MaxKeys=1000`, `ContinuationToken` as the cursor, keys stripped of the prefix, unsafe keys skipped.
   - `write`: `spool()` (storage.py, 50 MiB cap) then `put_object` with `IfNoneMatch="*"` or `IfMatch='"<etag>"'` when `conditional_put`; 409/412 -> `PreconditionFailed(await self.stat(path))`. Without it: HEAD just before the put (plan). `ServerSideEncryption` from `sse`. Single PUT only (files are at most 50 MiB): the plan's multipart above 16 MiB is not done; list it as a deviation.
   - `move`: read source (bounded), `write(dst, ..., if_match=None)`, then delete source. `delete`: `delete_object` (missing is a no-op). `read`: `get_object` body chunks; 404 -> `NotFound`. `stat`: `head_object`; 404 -> None; etag without quotes.
   - `health()`: `head_bucket`; with `health_write=True` also put+delete `.tumnis/health` (unconditional put to a Tumnis-owned key).
   - `probe_conditional_writes()`: write `.tumnis/probe-<uuid>`, then put again with `IfNoneMatch="*"` (412 -> `precondition_failed`, success -> `ignored`), then put with a stale `IfMatch` (same), delete the probe key.
   - Errors: botocore `ClientError` 5xx/429 -> `AdapterUnavailable`, other 4xx -> `AdapterRejected`, connection errors -> `AdapterUnavailable`; use `storage.call_storage` so storage answers never count against the breaker.
   - `aioboto3` is only a dev dependency: move `aioboto3==15.5.0` into `[project].dependencies` in `backend/pyproject.toml` and run `uv lock` (shared-file edit: report it). Import it with `# type: ignore[import-untyped]` as `tumnis/core/tests/integration/test_rclone.py` does.
   - Fill `s3_conditional_writes.yaml` with what the pinned chainguard MinIO actually answers (the red commit guessed `precondition_failed` for both; verify).
2. **Tables and api** (T-08, 09, 12, 14, then 13). New revision `backend/tumnis/modules/knowledge/migrations/0003_storage.py`: `revision = "knowledge_0003"`, `down_revision = "knowledge_0002"`, `phase = "expand"`, all with `create_tenant_table`: `storage_locations` (plan SQL, plus `ux_storage_locations_one_default` partial unique and a unique `(workspace_id, lower(name))` among live rows), `project_folders` (FKs to projects and storage_locations, unique `(workspace_id, project_id)`), `document_versions` (see Decisions), `pending_writes` (FKs to storage_locations and document_versions). Mirror them in `models.py`. Add `("storage_locations", "kind"): "server_path"` to `COLUMN_VALUES` in `backend/tumnis/core/tests/integration/row_factory.py`; check `backend/tests/meta/test_table_registry.py` and the isolation suite.
   - api (signatures already in `api.py`): `create_location` (validate kind/root; server path root absolute; S3 root `bucket/prefix`; SSRF check -> `ProblemError(422, "ssrf_blocked")`; seal the S3 config JSON with `settings_store.seal_for_workspace(session, workspace_id, blob, aad=b"storage_locations:<id>")`, id made with `tumnis.core.ids.uuid7()`; first location or `is_default=True` becomes default; run `check_location` logic to set status/status_reason/capabilities: server path `network_fs` from `is_network_fs(fstype)` via `/proc/self/mountinfo` on Linux, false elsewhere; S3 `conditional_put` from the probe), `list_locations`, `check_location` (health -> status; on offline->online drain `pending_writes` in insertion order, deleting each row once written; a `PreconditionFailed` whose current etag equals the content's sha256 counts as landed), `set_default_location` (versioned, `StaleVersion`), `assign_project_folder` (default location, `root_path = str(project_id)`, ON CONFLICT DO NOTHING; None when there is no default), `get_project_folder`, `set_project_location` (lists the folder on the old location: any file -> `ProblemError(409, "folder_not_empty")`; old location offline -> 409 `location_offline`), `write_project_file` (location offline in the row, or health degraded now -> mark offline and `ProblemError(409, "location_offline")`), `save_note` (snapshot the text document into `document_versions`, path `<root_path>/notes/<document_id>.md`; offline -> insert `pending_writes`, return `queued`; else write, return `written`), and `open_backend(session, location_id, *, net, resolver)` (TDD step 8 factory; in fakes mode a per-location `FakeStorage` kept in a module dict).
   - `events.py`: `@subscribe("project.created", name="knowledge.assign_project_folder")` calling `assign_project_folder` (never rename it).
3. **Router** (`router.py`, `v1_router("knowledge", prefixed=True)`, session auth, idempotent writes; pass `net=request.app.state.settings.net_policy()`): `GET /locations` (bare list, `unpaginated_reason`), `POST /locations` (201), `POST /locations/{location_id}/test`, `POST /locations/{location_id}/default` (body `{version}`), `PUT /projects/{project_id}/folder` (body `{location_id}`). Handlers look up the row (404) before any body rule (issue #28). Then `make gen` from the root (commit `schemas/`, `frontend/src/api/`, generated contract tests) and add `knowledgeListLocations` to `NOT_LIVE` (or LIVE_MAP) in `frontend/src/lib/live-map.ts`. Move `frontend/dist` aside before backend contract/integration runs.
4. **Frontend** (T-15): implement `frontend/src/components/settings/StorageSection.tsx` to the test (labels `Name`, `Kind` select with `server_path`/`s3`, `Folder path` or `Bucket and prefix`, `Endpoint`, `Access key`, `Secret key` (password input, cleared after submit), button `Add location`; each location a `<li>` with the name in a `<p>`, `Default`, `Online` or plain-words reason such as `Folder offline: marker file missing`, buttons `Test connection` and `Make default`). Add a `storage` section to `sections.ts` and `routes/settings.$section.tsx`, a `storageQuery` in `queries.ts`. After `make gen`, switch `frontend/src/test/msw/storage.ts` to the generated `LocationOut` type. Flip `test.fails` to `test`. Check at 375 px.
5. Refactor: every caller goes through `knowledge.api.open_backend`.
6. Part A of the plan: add the new names (A12 row for P1-14: `storage.py` names, `safe_prefix`, `spool`, `call_storage`, `tmp_location`, `HOSTILE_EXAMPLES`, revision `knowledge_0003`, tables, routes, subscriber).

## Decisions and deviations so far

- `unicodedata` added to `RULES_ALLOWED` in `backend/tests/meta/test_boundaries.py` and to the allow-list sentence in `AGENTS.md` (commit 314156c): `safe_rel_path` must NFC/NFKC-normalize and lives in `rules.py` per the plan; P1-15's `sanitize_filename` in `sync_rules.py` needs it too. It is a pure stdlib table module. Flag for Scott's OK (it widens a locked meta-test's data, not an assertion).
- `StorageError` and `PathRejected` live in `rules.py` (rules may import only their own `rules*`); `storage.py` re-exports them with the other errors.
- `Health` in `storage.py` is a small model (`status`, `reason`, `ok()`, `degraded(reason)`), as the plan's code uses it; unrelated to the registry's `Health` literal.
- Contract cases use P0-09's `subject` fixture name (the plan's sketch says `backend`); `StorageContract` subclasses `AdapterContract[StorageBackend]`.
- Two registry names, `knowledge.server_path` and `knowledge.s3`, both with `FakeStorage` as the fake; `TestFakeStorageForS3` reruns the fake suite under the S3 name so the P0-09 meta-test sees a fake contract for each.
- `safe_rel_path` also refuses a `~` segment anywhere and format characters (bidi overrides) except U+200C/U+200D; the T-04 generator requires that.
- `ServerPathStorage` refuses every symlink below the root (even inward ones), requires the marker for writes and moves (`LocationOffline`), lists only regular files and skips the marker, `.tumnis-tmp-*` and unsafe names. Etags are cached per (dev, inode, size, mtime_ns); a replace hashes afresh. New files are 0600 (plan).
- `test_b2_behavior_recorded` is a `skipif` on `TUMNIS_B2_*` environment variables instead of a new `manual` marker (no pytest marker registration change). The B2 row in the YAML is `null` until Scott runs it.
- Planned: `document_versions` created by P1-14 (minimal: `document_id` FK, `version_no int`, `content_hash bytea`, `body_md text`, `size bigint`) because `pending_writes.document_version_id` needs a target and no WP defines the table yet (A7 lists it under P1-14 to P1-17; P1-16 only "extends" it).
- Planned: the seed's storage locations (`homelab-minio`, one server path) are not added (needs a new seed kind in `tumnis/seed.py`); leave for P1-16 or a follow-up and say so.
- The api makes storage calls in-process (location save, test connection, folder checks): the architecture already has the api stream files from storage (`GET /v1/files/{id}`), so storage I/O is treated as allowed there.

## Gotchas

- Inside a class that defines `async def list(...)`, later annotations must say `builtins.list[...]` (the method shadows the builtin).
- `tests/contract/test_*.py` files are imported by a unit meta-test (`tumnis/core/tests/unit/adapters/test_adapter_registry.py`); keep their module-level imports free of containers.
- mypy checks the tests under `tumnis/` too; api names the integration tests use must exist.
- rtk filters pytest output; use `rtk proxy uv run pytest ...` to see results.
- `frontend/src/test/msw/dashboard.ts` fails `npm run typecheck` on main (missing `TaskStub`, from P0-18's merge); not ours, `make check` does not run `tsc`.
- The MinIO fixture listens on localhost, which the SSRF guard always blocks: T-13/T-14 reach it through this host's LAN address (`_lan_endpoint` in `test_locations.py`), so Docker must publish on all interfaces (it does by default).
- frontend `node_modules` is installed in this worktree (npm ci done).

## Verify

```bash
cd backend
uv run pytest -q tumnis/modules/knowledge -m "not integration"            # unit + fake/server-path contract
uv run pytest -q tumnis/modules/knowledge -m integration                    # MinIO, Postgres, DBOS (Docker)
uv run pytest -q -n auto -m "not integration and not contract"              # unit layer
uv run pytest -q -m contract ; uv run pytest -q -n auto -m integration       # after moving frontend/dist aside
cd .. && make check && make gen && git status --short
(cd frontend && npx vitest run src/components/settings/StorageSection.test.tsx)
```
