# P3-12 (Obsidian source) handoff (c2 -> c3)

Written 2026-10-02 on the context watcher's "HANDOFF NOW". Binding prompt:
`~/tumnis-coordinator/prompts/wave1/P3-12.txt` plus the coordinator notes
(`agent-reports/P3-12-coordinator-notes.md`). Plan section: `docs/IMPLEMENTATION-PLAN-DETAILED.md`
"P3-12 · Obsidian source" (around line 16546).

## State

- PR **#157** is READY for review: https://github.com/SpaceshipCreative/tumnis-guide/pull/157.
  The coordinator allowed marking it ready, with the wiring following in later commits on
  the same PR. The PR body source is `/tmp/claude-1002/P3-12-c2/pr-body.md`: copy it into
  your own scratch folder, update it, then run `gh pr edit 157 --body-file ...`.
- **Both dependencies are merged:**
  - #154 (P3-14 impl-2, knowledge_0009) merged in at f0c92b4c.
  - P3-02 (#156) merged on main at ea0706b3 and is merged into this branch at **24e5df31**
    (clean merge; `make gen` showed no drift). `make check` has NOT been run on 24e5df31 yet;
    run it first.
- All 12 spec tests are green with their markers removed (CI-cited):
  - T-01..06: run 36959617596.
  - T-09/10: run 36961890399 (unit).
  - T-07/08/11/12: run 36961890399 (integration-a).
- **CI on 9848d2c1** (the last head before the P3-02 merge): all green; `preview` pending as usual.
- **CodeRabbit:** one review on f0c92b4c, with 2 threads. Both are fixed and RESOLVED, and
  CodeRabbit accepted both replies. It answered "Already reviewed the last commit" for
  9848d2c1. The hourly quota is tight: after the wiring, request `@coderabbitai review` once.
  - git.py write probe fail-open -> fixed in 8d5e70ad (`_probe_refusal`: host key ->
    `host_key_changed`, network/DNS -> AdapterUnavailable, other -> `write_probe_inconclusive`).
  - HANDOFF.md stale #154 step -> 9848d2c1.

## Commits (c2)

| SHA | What |
|---|---|
| dddb0375 | `ObsidianSetup.tsx` + Vitest (props-driven: `hosted`, `projects`, `deployKey`, `onProbeHostKey(remote)`, `onPreview({settings, knownHosts})`, `onConnect({settings, knownHosts})`) |
| fb51552c | merge main (#152, #160) |
| f0c92b4c | merge main (#154): knowledge_0010 re-chained on knowledge_0009; `_refuse_synced` first in `api.delete_document` and `api._outside_file_target`; integration test `test_synced_notes_cannot_be_deleted_in_tumnis` |
| 8d5e70ad | write probe fails closed (CodeRabbit) + unit tests |
| 9848d2c1 | handoff notes |
| 24e5df31 | merge main (P3-02 #156) |
| this one | `chore: P3-12 handoff` |

## Remaining steps (the wiring), then finish

1. `make check` on 24e5df31 (use semgrep env vars in your scratch folder; see vm-agent-rules).
2. **Connection record, as decided after reading the merged P3-02.**
   - P3-02's tick (`integrations.workflows.connector_sync_tick`, every minute) syncs every
     connection of a provider registered with `register_provider` that is `ok`/`degraded`
     and due. It runs `connector_for(provider)` canonical-record pages, which do not fit a
     vault.
   - So do NOT `register_provider("obsidian")`. Make the connection row the way P3-13
     (S3, wp/P3-13) does: `integrations.seed_connection(s, "knowledge", "obsidian",
     "obsidian:<uuid>")`, a module-owned connection outside P3-02's framework tick.
   - Seal the deploy private key with `integrations.put_credentials(ctx, connection_id,
     {...})`, and report status with `integrations.set_connection_status`
     (`ok`/`error`, `last_error`, `last_sync_at`).
   - Don't change P3-02's helpers. Tell the coordinator about this choice; it is a
     deviation from the HANDOFF c1 plan (`register_provider`).
3. **Vault settings.** Add an `obsidian_vaults` tenant table to `knowledge_0010` (still
   unmerged, so editing it is fine; it stays chained on knowledge_0009), with its model in
   `knowledge/models.py`. Columns:
   - `connection_id` FK connections;
   - `mode` folder|git, `folder_path`, `remote`, `branch`;
   - `mapping` jsonb (folders, frontmatter_key, tag_prefix, unmapped, clippings_folder,
     extra_excludes);
   - `known_hosts` text (the pinned line), `pending_host_key` text (a probed line awaiting
     confirmation), `deploy_public_key` text;
   - version.

   P3-13 also adds a knowledge migration (knowledge_0011 on its branch); whichever merges
   later re-chains.
4. **Routes** (knowledge router, session auth; new `knowledge/obsidian/vaults.py` for the logic):
   - POST `/v1/knowledge/obsidian/vaults` creates the connection (pending) and the vault row.
     - Hosted mode (`net.mode == "hosted"`, see `api._refuse_hosted_server_path`) refuses
       folder with 422.
     - The folder path must be absolute with no `..` (reuse `api._server_root`).
     - Git generates an ed25519 deploy key (asyncssh `generate_private_key("ssh-ed25519")`;
       Context7 `/ronf/asyncssh` first), seals the private half and returns the public half.
   - POST `.../{id}/host-key/probe` runs `setup.probe_git_host` and stores the pending line,
     returning the sha256.
   - POST `.../{id}/host-key` takes {sha256} (re-probe and compare, as P3-14's
     `confirm_host_key` does) or {known_hosts}, then pins it via `setup.pinned_known_hosts`.
   - POST `.../{id}/preview` runs the preview on the worker (plan): a DBOS workflow running
     `sync.preview_mapping`, polled with a GET.
   - POST `.../{id}/connect` enqueues the first sync (git: `GitReader.connect`;
     `writable_deploy_key` / `write_probe_inconclusive` / `host_key_changed` become the
     connection's error status), then `sync_vault`.
   - GET list/one; DELETE disconnect (`integrations.disconnect` only works for framework
     connections, so soft-delete through a small owned path or `set_connection_status`).
5. **Worker:**
   - a DBOS workflow `knowledge_obsidian_sync(workspace_id, connection_id)` on the `sync`
     queue, deduplicated per connection;
   - a 15-minute tick in `knowledge/workflows.schedules()`, alongside the folder sync tick;
   - a folder-mode watcher copying `workflows.local_watch` (awatch, `debounce=5000`, roots
     from `obsidian_vaults` folder paths), started wherever `local_watch` is started (grep
     the worker);
   - the extraction hook: `vault_sync.register_extraction` / `enqueue_vault_extraction`
     (already in pipeline).
6. **Frontend:**
   - `make gen`;
   - a container that wires `ObsidianSetup` to the generated hooks (in Settings >
     Connections, or the knowledge rail: check where P3-02's Connections UI lives,
     `frontend/src/components/settings/connections/`);
   - `hosted` from the deployment mode the frontend already knows (grep `deployment_mode`
     in `frontend/src/api/types.gen.ts`);
   - 200 KB budget: lazy-load if it adds weight.
7. **Tests:** integration tests for the routes (hosted refuses folder, Git host key
   confirm/paste, preview, connect + sync, writable key -> error status) and a unit test
   for key generation. Run only your files locally; CI is the authority.
8. Finish:
   - `make check`, push, update the PR body, request `@coderabbitai review` once;
   - work the review loop until CI is green and no threads are open;
   - SendMessage main "#157 MERGE-READY at <sha>";
   - delete this HANDOFF.md in a chore commit when done.

## Decisions and deviations (keep in the PR body)

- knowledge_0010 (document_links) is chained on knowledge_0009.
- `VaultReader.list_files(skip)` predicate: excluded folders are never opened. `.git/` is
  always excluded.
- Links:
  - `ParsedNote.frontmatter_error` added.
  - Ambiguous bare link names stay unresolved; relative resolution applies only to targets
    with "/".
  - The project slug is `project_slug(name)`; an ambiguous slug maps to nothing.
- Documents:
  - The file hash is kept in `documents.source_revision`; the vault path in `path` and
    `external_id`; the source is `knowledge:obsidian`.
  - Notes are re-read on every scan, and only differences are written.
  - Trust is set at creation and only ever lowered (a note moved into Clippings); taint is
    never cleared.
  - Attachments are file Documents with no storage location, spooled to
    `<spool>/<version_id>`, with pipeline source `vault` (no place step).
    `download_info` can't serve them as files yet.
- DELETE, PATCH and the delete-at-source confirmation on a synced Document answer 409
  `read_only_source`, before #154's outcomes.
- GitReader:
  - `HostKeyAlias` is `[host]:port` off port 22 (OpenSSH's sshconnect.c).
  - A failed host key check becomes `host_key_changed`.
  - The write probe fails closed (CodeRabbit): it recognises read-only refusals by their
    wording.
  - `file://` remotes get no write probe and are refused in hosted mode.
- A symlink read in FolderReader is PathRejected.
- The contract suite has its own `git` helper.
- ObsidianSetup asks for the host key fingerprint to be typed back (it reuses P3-14's
  `HostKey`) or a known_hosts line to be pasted. Connect waits for a preview of the current
  settings.

## Scott items

- Git host key: confirm on first connect, then pin strictly; a pasted known_hosts line is
  also accepted (coordinator default, decision 82).
- Hosted mode offers the Git source only.
- DELETE on a synced Document answers 409 `read_only_source`.
- The write probe recognises read-only refusals by their wording (fail-closed): check it
  against the homelab Git host.

## Shared-file edits

- `deploy/Dockerfile`
- `backend/tumnis/core/tests/integration/row_factory.py`
- knowledge `api.py`, `router.py`, `pipeline.py`, `workflows.py`, `models.py` and
  `adapters/__init__.py`
- `backend/.importlinter`
- generated `schemas/openapi.json` and `frontend/src/api/*`

## Verify

```
cd backend
uv run pytest -q -p no:randomly tumnis/modules/knowledge/tests/unit -k obsidian
uv run pytest -q -p no:randomly tumnis/modules/knowledge/tests/contract/test_obsidian_vault_contract.py
# Docker-backed tests: CI integration-a is the authority
cd ../frontend && npx vitest run src/components/knowledge
```
