# P3-12 (Obsidian source) handoff (c1)

Written 2026-10-02 on the context watcher's "HANDOFF NOW". Binding prompt:
`~/tumnis-coordinator/prompts/wave1/P3-12.txt` plus the coordinator notes
(`agent-reports/P3-12-coordinator-notes.md`). Plan section: `docs/IMPLEMENTATION-PLAN-DETAILED.md`
lines 16546-16676.

## State

- PR **#157** (DRAFT): https://github.com/SpaceshipCreative/tumnis-guide/pull/157. Draft body only;
  the full body is ready at `/tmp/claude-1002/P3-12-c1/pr-body.md` (copy it; update test results).
  CodeRabbit not requested yet (request ONCE, when marking ready with `gh pr ready`).
- Backend is complete and ALL 12 spec tests are green with markers removed (CI-cited):
  T-01..06 (run 36959617596), T-09/10 (run 36961890399 unit), T-07/08/11/12 (run
  36961890399 integration-a). `make check` passes locally on 358fda84 (exit 0).
- Last CI run before the unmark commits (36961890399): unit and integration-a failed only on
  the XPASS(strict) markers (now removed); `performance` failed only on Lighthouse TBT
  (re-run that job once if it fails again: `gh run rerun <id> --failed`). Everything else green.
- P3-02 (#156) still DRAFT, not merged. Final wiring waits for the coordinator's "P3-02 merged".
- #154 (P3-14 impl-2, knowledge_0009) not merged yet.

## Commits (all pushed with this handoff)

| SHA | What |
|---|---|
| 3b77b0c7 | spec tests (red) (c0) |
| b62952b6 | parser and rules (c0) |
| c4ae8e45 | merge origin/wp/P3-12 into main-based worktree |
| 585073a5 | T-01..06 markers off (run 36959617596) |
| f50ee8c4 | vault readers (folder, Git, fake), contract suite, Dockerfile git + openssh-client |
| 125b62f6 | knowledge_0010 document_links, sync_vault, vault extraction source, 409 read_only_source |
| f2ea7747 | obsidian/setup.py host-key probe/pinning (decision 82), preview_mapping |
| 3c96d10e | T-09/10 markers off (run 36961890399) |
| 358fda84 | T-07/08/11/12 markers off (run 36961890399) |
| this one | `chore: P3-12 handoff` |

## Remaining steps

1. Wait for CI on the pushed head; confirm every job green (preview stays pending; re-run
   `performance` once if only TBT fails).
2. When #154 merges: merge origin/main, re-chain `knowledge/migrations/0010_document_links.py`
   `down_revision = "knowledge_0009"`, check `uv run alembic heads` (one knowledge head). Also fit
   `_refuse_synced` into #154's DELETE outcome handler (decision 81): the 409 `read_only_source`
   check runs before the 204/200/403 outcomes (currently in `api.trash` and `api.edit_document`).
3. After the coordinator's "P3-02 merged": merge origin/main, then wire:
   - register provider `obsidian` (kind `knowledge`, auth `none`) with P3-02's `register_provider`;
     use P3-02 only for the connection record/status (`create_connection`, `put_credentials` for the
     deploy key sealed, `set_connection_status`). Don't change P3-02's helpers.
   - knowledge-owned vault settings (mode folder|git, folder path or remote/branch, mapping,
     pinned known_hosts, pending key): either an `obsidian_vaults` table added to knowledge_0010
     or stored per P3-02's settings model; pick after reading the merged P3-02.
   - routes under `/v1/knowledge/obsidian/...`: probe host key (`setup.probe_git_host`), confirm/
     paste known_hosts (`setup.pinned_known_hosts`), connect (GitReader.connect, writable key ->
     422 `writable_deploy_key`), mapping preview (`sync.preview_mapping`, worker-computed per plan),
     sync now; a 15-minute scheduled `sync_vault` (DBOS); folder path: watchfiles with 5 s debounce
     + 15-min scan. Hosted mode (`DEPLOYMENT_MODE=hosted`): Git only (folder refused/hidden).
   - `frontend/src/components/knowledge/ObsidianSetup.tsx` (DS-01 primitives, reuse
     `components/settings/storage/HostKey.tsx` for fingerprint confirmation; mapping preview table;
     hosted shows Git only), Vitest test, `make gen` for the API client. 200 KB JS budget.
   - integration tests for the routes; Context7 for watchfiles if used.
4. `make check`, push, update PR body (`gh pr edit 157 --body-file ...`), `gh pr ready 157`,
   `gh pr comment 157 --body "@coderabbitai review"` once, run the review loop
   (`~/tumnis-coordinator/pr-review-loop.md`), then SendMessage main "#157 MERGE-READY at <sha>".
5. Delete this HANDOFF.md in a chore commit when done.

## Decisions and deviations (also in /tmp/claude-1002/P3-12-c1/pr-body.md)

- knowledge_0010 chained on knowledge_0008 for now (main's head) so CI migrates; re-chain after #154.
- `VaultReader.list_files(skip)` predicate: excluded folders never opened. `.git/` always excluded.
- `ParsedNote.frontmatter_error`; ambiguous bare link names unresolved; relative resolution only
  for targets with "/". Project slug = `project_slug(name)`; ambiguous slugs map to nothing.
- File hash in `documents.source_revision`; vault path in `path` and `external_id`; source
  `knowledge:obsidian`. Notes re-read every scan, only differences written.
- Trust set at creation, only ever lowered (moved into Clippings); taint never cleared.
- Attachments: file Documents with no storage location, spooled to `<spool>/<version_id>`,
  pipeline source `vault` (no place step); `download_info` can't serve them as files yet.
- DELETE and PATCH on synced Documents: 409 `read_only_source` (any `connection_id`).
- GitReader `HostKeyAlias` is `[host]:port` off port 22 (OpenSSH uses the alias as-is, no port:
  sshconnect.c `get_hostfile_hostname_ipaddr`). Failed host key check -> `host_key_changed`.
- A symlink read in FolderReader is PathRejected (was an outage before the contract test).
- Contract suite has its own `git` helper (import-linter forbids contract -> integration).
- `file://` remotes: no write probe, refused in hosted mode.
- Local integration runs needed `dangerouslyDisableSandbox` (sandbox blocks the Docker socket);
  CI is the authority.

## Scott items

- Git host key: confirm on first connect, then strict pin; pasted known_hosts line accepted
  (coordinator default, decision 82).
- Hosted mode offers the Git source only.
- DELETE on a synced Document answers 409 `read_only_source`.

## Shared-file edits

`deploy/Dockerfile`, `backend/tumnis/core/tests/integration/row_factory.py`, knowledge
`api.py`/`router.py`/`pipeline.py`/`workflows.py`/`models.py`/`adapters/__init__.py`, generated
`schemas/openapi.json` and `frontend/src/api/*` (route docstrings).

## Verify

```
cd backend
uv run pytest -q -p no:randomly tumnis/modules/knowledge/tests/unit -k obsidian
uv run pytest -q -p no:randomly tumnis/modules/knowledge/tests/contract/test_obsidian_vault_contract.py
# Docker-backed (CI integration-a is the authority):
uv run pytest -q -n 3 tumnis/modules/knowledge/tests/integration/test_obsidian_sync.py tumnis/modules/knowledge/tests/integration/test_obsidian_git.py
```
