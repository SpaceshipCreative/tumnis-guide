# P3-12 (Obsidian source) handoff (c3 -> c4)

Written 2026-10-02 by c3 near the context limit. Binding prompt:
`~/tumnis-coordinator/prompts/wave1/P3-12.txt` plus the coordinator notes
(`agent-reports/P3-12-coordinator-notes.md`). Plan section: `docs/IMPLEMENTATION-PLAN-DETAILED.md`
"P3-12 · Obsidian source" (line ~16549).

## State

- PR **#157** (READY): https://github.com/SpaceshipCreative/tumnis-guide/pull/157. PR body source
  from c2: `/tmp/claude-1002/P3-12-c2/pr-body.md`; copy it to your own scratch folder, update it
  (wiring, deviations below, docs cited), then `gh pr edit 157 --body-file ...`.
- P3-02 (#156) and #167 are merged into the branch (24e5df31). `make check` on 24e5df31 was green
  (c3, before the wiring).
- All 12 spec tests are green with their markers removed (CI runs 36959617596, 36961890399).
- **The wiring is written and committed by c3 (the commit after this handoff's parent).** CI has
  NOT run on it yet: push is done; read `gh pr checks 157`. The new integration tests have never
  run (Docker): CI integration-a is their first run.
- CodeRabbit: per the coordinator's 07:10Z note, its quota is spent: do NOT request a full review;
  only check existing threads (both earlier threads are resolved).

## What c3 built (the wiring)

- `knowledge_0010` (still down `knowledge_0009`) now also makes `obsidian_vaults` (connection_id
  FK, mode folder|git, folder_path, remote, branch, mapping jsonb, known_hosts, deploy_public_key,
  status pending|connecting|ok|error, last_error, last_sync_at); model `ObsidianVault`;
  `row_factory.py` entries for both CHECKs.
- `knowledge/obsidian/vaults.py`: api half (create_vault, list_vaults -> {folder_allowed, vaults},
  get_vault, probe_host_key (stateless, nothing stored), start_preview + preview_result (DBOS
  workflow `knowledge_obsidian_preview`, polled via `DBOSClient.retrieve_workflow_async(...)
  .get_status()`; preview id `obsidian-preview:<connection>:<uuid7>`, prefix checked), connect
  (saves settings + pinned known_hosts via `setup.pinned_known_hosts`, enqueues
  `knowledge_obsidian_connect`), delete_vault (soft-delete, sealed key overwritten with {},
  connection `disabled`)); worker half (run_preview, run_connect, run_sync, vaults_to_sync with
  clone pruning, watched_vaults, watched_vault). Test seam `use_git_runner(factory, resolver=)`.
  `new_deploy_key()` (asyncssh ed25519, OpenSSH private/public).
- Connection row: `integrations.upsert_connection(kind="knowledge", provider="obsidian",
  account="obsidian:<uuid7>", status="pending_auth")` (as P3-13 does; the coordinator's note said
  seed_connection, but seed_connection is a dev/test tool). Private key sealed with
  `put_credentials`; status via `set_connection_status`: refused (writable key, host key changed,
  probe inconclusive) -> vault `error` + connection `auth_required`; unreachable during a sync ->
  vault stays `ok` with last_error + connection `degraded`; success -> `ok` + last_sync_at.
  No `register_provider` (coordinator-approved).
- `api._refuse_synced`: a Document of a DISCONNECTED vault (only deleted vault rows for its
  connection) is editable again; every other connection's Documents still answer 409
  `read_only_source`.
- `settings.KnowledgeSettings.obsidian_dir` (default `/var/lib/tumnis/obsidian`, env
  `KNOWLEDGE__OBSIDIAN_DIR`) and `pipeline.current()`.
- `workflows.py`: `knowledge_obsidian_preview|connect|sync` (+ steps), `enqueue_vault_sync`
  (dedup `obsidian-sync:<cid>`), `knowledge-obsidian-sync-tick` (15 min, sync queue) in
  `schedules()`, `vault_watch(stop)` (awatch, 5 s debounce). `worker.py` starts `_vault_watch`
  beside `_folder_watch`.
- Routes (router.py, end block, session auth): GET/POST `/knowledge/obsidian/vaults`, POST
  `/knowledge/obsidian/host-key/probe`, GET/DELETE `/knowledge/obsidian/vaults/{connection_id}`,
  POST `.../preview` (202), GET `.../preview/{preview_id}`, POST `.../connect` (202).
- Frontend: `ObsidianSection.tsx` (container: list with status/last error/Disconnect, "Add a
  vault" -> ObsidianSetup; a draft vault is made on the first step that needs one: Git on the
  first host-key probe so the deploy key shows before Preview clones, folder on first preview;
  preview polled with `client.query`), Settings section `obsidian` ("Obsidian", appended after
  voice to avoid P3-13's `sources` insertion), `live-map.ts` NOT_LIVE entries, `make gen` output.
- Tests: unit `tests/unit/test_obsidian_vaults.py` (3, green locally); integration
  `tests/integration/test_obsidian_vaults.py` (3: hosted git-only + sealed key + delete; folder
  preview -> connect -> sync with the `dbos` fixture via `extract_env`; writable key refused via
  FakeGitRunner + ScriptedResolver) - unrun locally, CI is the authority.

## Next steps (c4)

1. `gh pr checks 157`; fix whatever fails (likely candidates: the new integration tests; meta tests
   on routes/openapi/settings sections; the Settings route tests if a section list is asserted;
   a Vitest for every Settings section, if one exists).
2. Add a small Vitest for `ObsidianSection` (MSW handlers for the new routes; onUnhandledRequest
   is "error") if the frontend coverage rules want one; check the 200 KB bundle (`make check`
   runs check_bundle).
3. Third-party docs to cite in the PR body: DBOS 3.1.0 `DBOSClient.retrieve_workflow_async` /
   `WorkflowHandleAsync.get_status` (Context7 /dbos-inc/dbos-docs, confirmed in the installed
   dbos/_client.py); asyncssh 2.24.0 `generate_private_key("ssh-ed25519")`, `export_private_key`,
   `export_public_key` (Context7 had no match; verified against the installed package: confirm on
   asyncssh's own docs, https://asyncssh.readthedocs.io/en/latest/api.html, with WebFetch, as key
   generation is security-sensitive); TanStack Query `queryClient.query` (fetchQuery is
   deprecated in the pinned version per its own types); watchfiles `awatch` as already used.
4. Update the PR body; when CI is green and no threads are open, delete this HANDOFF.md in a chore
   commit, push, then SendMessage main "#157 MERGE-READY at <sha>".

## Decisions and deviations (keep in the PR body)

- knowledge_0010 (document_links + obsidian_vaults) is chained on knowledge_0009. P3-13 owns
  knowledge_0011; whichever merges later re-chains.
- Connection via `upsert_connection` (not `seed_connection`, not `register_provider`); vault status
  lives in `obsidian_vaults` because P3-02's reads list framework providers only.
- Disconnect is module-owned: `integrations.disconnect` is framework-only, so the vault row is
  soft-deleted, the sealed key overwritten with `{}` and the connection set `disabled` (the
  connection row is not soft-deleted). Its Documents stay and become editable.
- Host key probe is stateless (the component probes by remote before a vault exists); connect and
  preview carry the known_hosts line, checked by `setup.pinned_known_hosts`.
- Preview result read from the DBOS workflow status (no table column).
- `VaultReader.list_files(skip)` predicate: excluded folders are never opened. `.git/` is
  always excluded.
- Links: `ParsedNote.frontmatter_error` added; ambiguous bare link names stay unresolved; relative
  resolution only for targets with "/"; project slug `project_slug(name)`, an ambiguous slug maps
  to nothing.
- Documents: file hash in `documents.source_revision`; vault path in `path` and `external_id`;
  source `knowledge:obsidian`; notes re-read every scan, only differences written; trust set at
  creation and only lowered; taint never cleared; attachments are file Documents with no storage
  location, spooled to `<spool>/<version_id>`, pipeline source `vault` (no place step);
  `download_info` can't serve them as files yet.
- DELETE, PATCH and the delete-at-source confirmation on a synced Document answer 409
  `read_only_source`, before #154's outcomes.
- GitReader: `HostKeyAlias` is `[host]:port` off port 22; a failed host key check becomes
  `host_key_changed`; the write probe fails closed (recognises read-only refusals by wording);
  `file://` remotes get no write probe and are refused in hosted mode.
- A symlink read in FolderReader is PathRejected. The contract suite has its own `git` helper.
- ObsidianSetup asks for the fingerprint to be typed back (P3-14's `HostKey`) or a known_hosts
  line pasted; Connect waits for a preview of the current settings.

## Scott items

- Git host key: confirm on first connect, then pin strictly; a pasted known_hosts line is also
  accepted (coordinator default, decision 82).
- Hosted mode offers the Git source only.
- DELETE on a synced Document answers 409 `read_only_source` (a disconnected vault's Documents
  become editable).
- The write probe recognises read-only refusals by their wording (fail-closed): check it against
  the homelab Git host.

## Shared-file edits

- `deploy/Dockerfile`; `backend/tumnis/core/tests/integration/row_factory.py`;
  `backend/tumnis/settings.py`; `backend/tumnis/worker.py`
- knowledge `api.py`, `router.py`, `pipeline.py`, `workflows.py`, `models.py`,
  `adapters/__init__.py`, `migrations/0010_document_links.py`
- `backend/.importlinter`
- frontend `components/settings/sections.ts`, `routes/settings.$section.tsx`, `lib/live-map.ts`
- generated `schemas/openapi.json` and `frontend/src/api/*`

## Verify

```
cd backend
uv run pytest -q -p no:randomly tumnis/modules/knowledge/tests/unit -k obsidian
uv run pytest -q -p no:randomly tumnis/modules/knowledge/tests/contract/test_obsidian_vault_contract.py
# Docker-backed tests: CI integration-a is the authority
cd ../frontend && npx vitest run src/components/knowledge
```
