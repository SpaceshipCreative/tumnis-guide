# P3-12 (Obsidian source) handoff

Written 2026-10-01 on the context watcher's "HANDOFF NOW". Binding prompt:
`~/tumnis-coordinator/prompts/wave1/P3-12.txt` (read it fully again). Plan section:
`docs/IMPLEMENTATION-PLAN-DETAILED.md` lines 16546-16676.

## State

- Branch `wp/P3-12`, worktree `.claude/worktrees/agent-aaed9e31b100a371c`.
- No PR yet. No CI run yet (CI runs on PRs; push alone started nothing).
- P3-02: `origin/wp/P3-02` now exists, but no PR and not merged. Read its interfaces
  read-only; final wiring and the PR wait for the coordinator's "P3-02 merged".

## Commits

| SHA | What | Pushed |
|---|---|---|
| 3b77b0c7 | `test(knowledge): P3-12 spec tests (red)`: T-P3-12-01..12 as strict xfail, fixture vault, links table data | yes |
| b62952b6 | `feat(knowledge): Obsidian note parser and vault rules`: `obsidian/parse.py`, `obsidian/rules.py`, `test_obsidian_resolve.py`, `.importlinter` (rules-are-pure gains the two modules) | yes (with this handoff) |
| this one | `chore: P3-12 handoff` | yes |

T-01..06 pass locally with `--runxfail`; markers stay until CI shows XPASS(strict)
(decision 78: remove only then, cite the CI run id, never change an assertion).

## Uncommitted work in progress (NOT in git)

Saved at `/tmp/claude-1002/P3-12-c0/wip/obsidian/` (moved out of the tree so `make check`
passes for this commit). Move it back to
`backend/tumnis/modules/knowledge/adapters/obsidian/` and add an empty `__init__.py`:

- `port.py`: `VaultReader` Protocol (`list_files(skip=never_skip)`, `read`, `refresh`),
  `Skip`, `FileStat` (re-exported from storage; etag = sha256 hex).
- `folder.py`: `VaultFiles` (fd-based `os.fwalk`, prunes `.git` and `skip`, O_NOFOLLOW,
  sha256 cache per (dev, ino, size, mtime_ns), skips files changing while hashed),
  `run_files` (to_thread through `Adapter.call`), `FolderReader` ("knowledge.obsidian_folder").
- `git.py`: `GitReader` ("knowledge.obsidian_git"), `parse_remote`, `SubprocessGitRunner`,
  `GitRunner` Protocol, `InvalidRemote`, `WritableDeployKey`, `PROBE_REF`, hardened env
  (GIT_ALLOW_PROTOCOL, GIT_TERMINAL_PROMPT=0, GIT_CONFIG_NOSYSTEM, GIT_CONFIG_GLOBAL=/dev/null,
  `-c protocol.ext.allow=never`, `core.hooksPath=/dev/null`, GIT_SSH_COMMAND with
  `-F /dev/null -i key IdentitiesOnly StrictHostKeyChecking=yes UserKnownHostsFile BatchMode
  HostKeyAlias=<host> HostName=<checked ip>`), key and known_hosts written 0600 per command
  then deleted; `resolve_and_check` before every network command (ports policy | {22, 2222}).
- `fake.py`: `FakeVault` (in-memory, `script(path, bytes|None)`, `calls`), `FakeGitRunner`
  (`script(verb, returncode=, stderr=)`, `calls` of `GitCall(argv, env, cwd, key_seen)`,
  clone makes `<dest>/.git`). NOTE: T-09/T-10 expect a default push (probe) to fail;
  check the default push result is non-zero (e.g. rc 128 "read-only") before running them.

None of these has been run yet. 203.0.113.10 (T-09's resolver answer) is not in
`core/net.py` ALWAYS_BLOCKED, so `resolve_and_check` passes it in self-hosted mode.

## Remaining steps (plan TDD order)

1. Restore the WIP adapters; register `knowledge.obsidian_folder` and
   `knowledge.obsidian_git` in `knowledge/adapters/__init__.py` (`register_adapter(name,
   port=, real=, fake=)`; fakes built by `resolve(name, "fake")` with no args).
   Run `cd backend && rtk proxy uv run pytest -q --runxfail -p no:randomly
   tumnis/modules/knowledge/tests/unit/test_obsidian_git.py` (T-09, T-10), then ruff/mypy.
2. Contract suite `tests/contract/test_obsidian_vault_contract.py` (base
   `AdapterContract[VaultReader]`; TestFakeVault "fake", TestFolderReader "real",
   TestGitReader "real" over a local bare repo).
3. Migration `knowledge_0008` `document_links` (check `alembic heads`; main knowledge head
   was knowledge_0007; no core migration) + `DocumentLink` model in `knowledge/models.py`
   (columns used by tests: from_document_id, to_document_id NULL, kind link/embed,
   to_target, heading) + table registry meta test.
4. `pipeline.py`: extraction Source "vault" (read from spool, no place) — minimal edit.
5. `knowledge/obsidian/sync.py` `sync_vault(ctx, connection_id, reader, mapping, extract=)`
   per the design in the T-07/T-08 tests: templates folder from `.obsidian/templates.json`,
   renames by file hash keep the document, `_write_text` for new/changed notes, trash
   missing, restore reappearing, links two-pass and re-resolved every scan, embedded
   attachments as file documents (pending_scan, spool `<spool>/<version_id>`, extract hook),
   idempotent second sync. Do NOT use P3-02's `upsert_synced_documents` (forces
   untrusted+tainted; cannot carry links, versions, attachments).
6. Read-only guard: `edit_document` raises 409 `read_only_source` when `connection_id` is
   set (T-12).
7. Dockerfile: add `git` and `openssh-client` (shared file; report it).
8. `make check` (semgrep needs `SEMGREP_SETTINGS_FILE=/tmp/claude-1002/P3-12-c0/semgrep-settings.yml
   SEMGREP_LOG_FILE=/tmp/claude-1002/P3-12-c0/semgrep.log
   SEMGREP_VERSION_CACHE_PATH=/tmp/claude-1002/P3-12-c0/semgrep-version`), commit, push.
9. After "P3-02 merged": merge origin/main, wire connection settings, scheduling,
   routes, mapping preview, `ObsidianSetup.tsx` (DS-01 primitives, `make gen`, hosted mode
   shows Git only); open the PR (body in `/tmp/claude-1002/P3-12-c0/`, docs cited);
   `@coderabbitai review` once; get CI XPASS(strict) for all 12, then remove the markers
   citing the run id; review loop; SendMessage "main": "#<PR> MERGE-READY at <sha>".

## Decisions and deviations (for the PR body and final report)

- Project slug = normalized project name (`project_slug`); projects have no slug column.
- `file://` remotes: no write probe (no key; a local dry-run push would succeed); refused
  in hosted mode (SsrfBlocked).
- `list_files` takes an extra `skip` predicate so excluded folders are never opened.
- `ParsedNote.frontmatter_error` added; `.git` always excluded.
- Red spec tests import not-yet-existing modules through `importlib` (mypy), as P4-03 did.
- Ambiguous bare link names stay unresolved; relative resolution only for targets with "/".
- T-01 dropped the indented-code and `(#paren)`/heading-tag ambiguous cases before the red
  commit.

## Scott items

- Known-hosts pinning: first-connect confirmation of the host key vs pasted known_hosts.
- Hosted mode offers Git only (no mounted folder).

## Coordination

- P3-13 (`wp/P3-13`) and P3-14 (`wp/P3-14-clean`) also edit knowledge rules/router/models;
  keep P3-12 edits additive; whoever merges later re-chains the knowledge migration.

## Verify

```
cd backend
rtk proxy uv run pytest -q -n 3 --runxfail -p no:randomly tumnis/modules/knowledge/tests/unit/test_obsidian_parse.py tumnis/modules/knowledge/tests/unit/test_obsidian_rules.py tumnis/modules/knowledge/tests/unit/test_obsidian_resolve.py
```
