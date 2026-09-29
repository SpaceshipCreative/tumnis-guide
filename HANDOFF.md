# HANDOFF: PR #47 (wp/P1-09, Google Calendar connector)

The session was paused by the coordinator. Every commit is pushed. The last code head is `8e34402`. Do not merge (the coordinator merges).

## What was done
- `10be802`: merged `origin/main` (P1-01, P0-20 search, #54 CI fixes).
  - Conflicts: `row_factory.COLUMN_VALUES` and the plan-table rows. Both sides were kept.
  - `WorkerKiller` has one implementation; `_deliveries` now reuses `_dbos()`.
  - `wiring.load_workflows()` is defined once, and main does not have it yet.
  - `alembic heads` shows one head per branch.
- `1ae5d90` (red) and `a94c42b` (fix): a refused `list_calendars` (`AdapterRejected`) or a list with no primary calendar ends the OAuth exchange and uses the grant up. `AdapterUnavailable` still retries.
- `55b6922` (red) and `e274a36` (fix): per-account sync lease on the new column `calendar_accounts.sync_owner`, added to `calendar_0002`.
  - The lease is a compare-and-set on the account row, taken in `begin_sync` and released in `finish_sync`.
  - A second sync ends `busy` while the holder is PENDING or ENQUEUED.
  - A holder that finished or failed for good is replaced.
  - New api functions: `claim_sync`, `release_sync`, and a `complete_sync(owner=)` keyword.
- `8e87daf` (red) and `8e34402` (fix): a reconnect keeps the selection only within the calendars still listed (falling back to the primary) and soft-deletes the dropped calendars' events. The pipe in the P1-09 plan-table row is escaped.

## CodeRabbit threads
- Done and resolved: "Consume the OAuth grant for permanent connection failures" (`a94c42b`).
- Done (outside-diff item, answered in a PR comment): "Serialize `connector_sync` per connection" (`e274a36`).
- Done and resolved: "Prune `selected_calendar_ids` on reconnect" (`8e34402`).
- Done and resolved: "Escape the pipe in `MappedEvent | Tombstone`" (`8e34402`).
- Open threads: none (0 unresolved).
- Review of `8e34402`: `@coderabbitai review` got the answer "Already reviewed the last commit", but no new review object or comments appeared within about 10 minutes. The next agent should check for a new review. If nothing arrives, consider `@coderabbitai full review` (quota: 3 of 10 per hour were left at 18:29Z).

## CI on 8e34402
- ci: success
- version-skew: success
- preview: queued. There is no homelab runner, so ignore it.

## Local verification (after the last fix)
- `make check`: green (unit 926 passed; Vitest 51 passed).
- `npm --prefix frontend run typecheck`: green.
- Contract: 172 passed.
- Calendar module: 97 passed.
- Full integration (before `8e34402`): 670 passed, 1 failed.
  - The failure is `core/tests/integration/test_rclone.py`: a MinIO timeout on this VM.
  - This PR does not touch rclone, and CI is green on it.
- VM flakes seen here: Docker "address already in use" port collisions from concurrent agents, and a ProjectList Vitest timeout under load. Both pass on rerun.

## For Scott
- All-day busy rule (not decided here): Google leaves out `transparency` for busy all-day events, and the plan's rule counts those events as free. A one-line fix would be `raw.get("transparency", "opaque")`.
- Commit identity: the commits from this session are authored "Claude (agent) <noreply@anthropic.com>". Committing as Scott was refused by the permission classifier.
