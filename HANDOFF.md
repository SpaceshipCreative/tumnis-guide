# HANDOFF: P2-03 Project and workspace digests

Stopped on the coordinator's "STOP NOW" (VM out of RAM). No PR is open yet. No containers or
test runs of mine are left running (the one integration attempt failed at once: the sandbox
blocks the Docker socket, so nothing started).

Branch: `wp/P2-03` on origin (push with `/usr/bin/git push origin HEAD:wp/P2-03`). Local
branch in this worktree is also named `wp/P2-03` (the rename worked; the config write failed
harmlessly).

## Commits

| SHA | What |
| --- | --- |
| 1b1cef8 | `test(agents): P2-03 spec tests (red)`: T-P2-03-01 to -10 as strict xfails, helper `_digest.py` |
| 789a489 | `feat(agents): digest cursor rules and event classification` (T-02, T-03 green, markers removed) |
| (this WIP commit) | tables, migration, append/read, subscribers, ops, twins, events, schemas; T-09 marker removed but NOT yet run |

`make check` was green at 789a489. The WIP commit was made without `make check` (stop
order); ruff, ruff format, mypy and lint-imports were clean just before the last edits
(`tests/_mcp.py` SAMPLES and fixtures were added after; re-run `make check` first).

## State of the spec tests

- Green, markers removed: T-P2-03-02 `test_resolve_start_table`, T-P2-03-03
  `test_classify_event_kinds` (plus the extra `test_classify_event_data`), all in
  `backend/tumnis/modules/agents/tests/unit/test_digest_rules.py`.
- Marker removed, not yet run: T-P2-03-09 `test_duplicate_event_delivery_one_entry`
  (`test_digest_content.py`). Needs the integration layer (Docker): run `make test-int`
  bare from the worktree root, or rely on CI.
- Still `xfail(strict=True, reason="spec:P2-03")`: T-01 (both methods of
  `TestDigestMachine` in `test_digest_exactly_once.py`), T-04, T-05, T-06, T-07, T-08
  (`test_digest_content.py`), T-10 (`backend/tests/meta/test_no_memory_client.py`).

## What is implemented (WIP commit)

- `agents/models.py`: `DigestEntry`, `DigestCursor`. Migration
  `agents/migrations/0003_digests.py`, revision **`agents_0003`** (down `agents_0002`,
  expand): `digest_entries` (tx numeric(20) default `pg_current_xact_id()::text::numeric`,
  seq identity always, unique (workspace_id, event_id, kind), project/workspace/kind
  indexes, kind and scope checks) and `digest_cursors` (unique (workspace_id, consumer_id,
  scope_key)).
- `agents/rules.py`: `Pos`, `resolve_start`, `DigestCursorInvalid`, `DIGEST_KINDS`,
  `DIGEST_EVENTS`, `ALSO_IN_WORKSPACE`, `TaskFacts`, `DigestEvent`, `EntrySpec`,
  `digest_task_id`, `classify_event`; plus P2-02's `escape_untrusted`, `escape_attr`,
  `render_block` exactly as the plan's P2-02 section sketches (seam: P2-02 has no PR yet).
- `agents/digest.py`: `append_entry` (ON CONFLICT DO NOTHING), `record_event` (task facts
  via `tasks.api.get_task`), `read_digest` (cursor row upsert + FOR UPDATE, `resolve_start`,
  one-statement horizon query, ack + issued=max, entries rendered: agent comments and linked
  items in untrusted blocks), HMAC cursor codec (`base64url(tx:seq:consumer:scope_key).tag`,
  key from `settings_store.purpose_key(..., "digest-cursor")`), `DigestOut`
  (`@versioned("digest", "digest", 1)`, with `gap`), `horizon_lag_seconds`.
- `agents/api.py`: re-exports; `export_metrics` sets `tumnis_digest_horizon_lag_seconds`.
- `agents/events.py`: subscribers `agents.digest_<event>` for every `DIGEST_EVENTS` entry
  (by name; document.* and focus.* payload models belong to P1-16 / P2-15).
- `agents/mcp.py`: ops `get_project_digest`, `get_workspace_digest` (tasks:read; consumer =
  `caller.profile_id or caller.key_id`; a project-limited caller never sees another
  project's entries). `agents/router.py`: twins `GET /v1/digests/project/{project_id}`,
  `GET /v1/digests/workspace`. `core/agent_surface.py`: the two PENDING_TOOLS entries removed.
- `core/settings_store.py`: `purpose_key(session, workspace_id, purpose)`.
- `tasks/payloads.py` + `tasks/api.py`: new events `task.commented` (from `add_comment`) and
  `context_item.linked` (from `link_context_item`, only for a new or restored link);
  re-exported in `tasks/events.py`.
- `integrations/api.py`: `ContextItemText`, `context_item_text(ctx, id, session=)`.
- `tests/_mcp.py`: SAMPLES for the two ops. Contract fixtures
  `tests/contract/fixtures/{events/task.commented,events/context_item.linked,digest/digest}/v1.json`;
  `make gen` output committed (schemas, openapi, tools.json, frontend client, generated tests).

## Exact next steps

1. `make check` (use the semgrep env vars: `SEMGREP_SETTINGS_FILE`, `SEMGREP_LOG_FILE`,
   `SEMGREP_VERSION_CACHE_PATH` under `$TMPDIR/P2-03-c<n>/`; a wrapper was at
   `$TMPDIR/P2-03-c0/check.sh`). Fix lint (e.g. ARG001 on `_get_workspace_digest` in
   `tests/_mcp.py` if ruff flags it).
2. Run the integration layer (`make test-int` bare, or push and read CI). Expect to debug:
   the horizon SQL parameters (`:also` text[], `:project_ids` uuid[] possibly empty,
   `NOT :limited` bool), `DigestOut` as a VersionedPayload in the MCP output schema, the
   parity sweeps T-P2-01-02/03/04 for the two new ops.
3. Remove markers one at a time in the plan's order: T-09 (now), T-01 (both methods),
   T-07/T-08, T-04/T-05/T-06, then T-10 after adding to `backend/.importlinter`:

   ```ini
   [importlinter:contract:no-memory-client]
   name = Tumnis never imports a memory-system client (FR-13.5)
   type = forbidden
   source_modules =
       tumnis
   forbidden_modules =
       hindsight
       hindsight_client
       vectorize
   ```
   (source `tumnis` is exactly what T-10 checks; confirm `lint-imports` accepts a root
   package as a source module.)
4. Check 100% coverage of `resolve_start` and `classify_event`.
5. Merge origin/main, push, open the PR (`gh pr create --base main --head wp/P2-03 --title
   "[P2-03] impl: Project and workspace digests" --body-file ...`), comment
   `@coderabbitai review` once, run the review loop.

## Deviations from the plan (for the PR body)

- Property test writers are SQLAlchemy async sessions on psycopg (app role, explicit
  transactions), not asyncpg: asyncpg is not pinned and a test should not add a dependency.
- The pinned out-of-order example is a separate deterministic test method driving the same
  machine (`test_out_of_order_commit_is_not_skipped`); Hypothesis's `@example` does not
  apply to a `RuleBasedStateMachine`.
- `label_override` counts `human.decided` with item_kind `label_override` (P1-07's actual
  name) and `label` (not when the proposal was accepted); the plan table says `label`.
- `human.decided` carries no project: the subscriber reads the task's project through
  `tasks.api.get_task` (target task, or `payload.task_id`), then classifies (pure).
- New events `task.commented` and `context_item.linked` (no WP defined them; the plan's
  digest table names them) are emitted by tasks.
- `render_block`/`escape_untrusted` added to agents `rules.py` ahead of P2-02 (smallest seam).
- Emitters not merged yet are stood in by `_digest.py` helpers: `human.decided` written to
  the outbox from the core event registry's model; `document.*` (P1-16) and
  `focus.level_changed` (P2-15) handed straight to the subscriber.
- No `digest_consumer` pytest fixture: `_digest.DigestConsumer` does the same job.
- Seed "Standing rules" workspace document not added (seed writer and seed tests untouched).
- `gap: true` (retention) is a field only; the 90-day housekeeping of entries is not built.
- Cursor MAC key derives from the active workspace data key; a key rotation invalidates
  outstanding cursors (400 `invalid_cursor`, the skill re-reads from its acked position).

## Scott items

- Done-checklist item "the master profile on the homelab reads a real project digest with
  its key (retained once P2-12 lands)" needs the homelab; not doable here.
- Nothing else needs Scott so far.
