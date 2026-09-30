# HANDOFF: P2-07 Runner daemon v2 (continuation 2)

Stopped on the coordinator's "HANDOFF NOW" (context watcher). Clean point: every commit
below passed ruff, mypy and the layers noted; nothing uncommitted. No PR yet.

## State

- Branch: pushed as `HEAD:wp/P2-07` (based on main `27bef71`, P1-05 and #64 merged).
- Commits (this continuation, on top of `a080719` red tests and `401584b` first handoff):
  - `f6906f8` feat(agents): protocol 2 message models and version negotiation
  - `cdf67f0` feat(daemon): outbox, cancel, worktrees, hardened units
  - `532fbab` feat(agents): protocol 2 on the server (acks, dedupe, artifacts, cancel)
  - this handoff commit
- PR: none. CI: not run on this code yet. Review threads: none.
- Alembic: `agents_0002` (down `agents_0001`, branch agents, phase expand), squawk passed
  locally. Adds `runs.profile_version` (Part A shared name; P2-04 builds on it) and widens
  `ck_run_events_kind` with `artifact`, `status` (re-added NOT VALID; no VALIDATE in the
  same transaction, which squawk flags).

## Spec tests

| ID | Status |
| --- | --- |
| 01, 11 | green, marker removed (`f6906f8`) |
| 13 | green, marker removed (`532fbab`) |
| 03, 06, 07, 08, 09, 10, 14, 18 | green, markers removed (`cdf67f0`); daemon suite 26 passed + new protocol-2 schema test; property test also green with `CI=1` (500 examples) |
| 02, 04, 05, 12, 16, 17 (`test_runner_dedupe.py`), 15 (`tests/integration/test_daemon_e2e.py`) | code written, MARKERS STILL ON, not yet run against the new code |

Confirmed before implementing (coordinator's request): a `make test-int` run showed T-02,
04, 05, 12, 15, 16[2], 17 XFAIL for the right reasons. T-16[1] (protocol-1 acks) XPASSED
(strict, so reported FAILED): it tests unchanged P1-04 behavior and was never red; the
marker comes off with the rest, which resolves it. Report it as a note, not a code issue.

## Next steps (exact)

1. Remove the `@pytest.mark.xfail(strict=True, reason="spec:P2-07")` lines from
   `backend/tumnis/modules/agents/tests/integration/test_runner_dedupe.py` and
   `backend/tests/integration/test_daemon_e2e.py` (a python one-liner per file; the git
   hook refuses compound commands that mention git or use variables/heredocs, so keep
   commands plain and write scripts to the scratchpad with the Write tool).
2. Run `make test-int` bare from the worktree root (takes ~20 min under load), or push and
   read CI. Fix what fails. Likely spots if something fails:
   - `ws.py` `_run_report` / `_status` / `_insert_event` (stream, status, artifact),
     `_ack` batching (`ACK_BATCH_S` timer task), `_render` of mailbox rows per protocol.
   - `hermes.py` `DaemonTransport.cancel` (protocol-1 fallback marks the run cancelled with
     `OLDER_RUNNER`, `failed` event uuid5(run,"failed"), best-effort `DBOS.send_async`).
   - T-15: daemon runs as `python -m tumnis_daemon.main run --config ...` with
     `PYTHONPATH=daemon` on the backend venv; it registers with protocol [1,2] and
     capabilities run, health, stream, cancel, upload_artifact, worktree.
   Other failures seen in the same local run, unrelated to P2-07 (load/known):
   `test_rclone_copy_keeps_files_removed_at_source` (known local-only),
   `test_relay.py::test_worker_killed_after_handler_step_does_not_rerun_it`,
   `test_relay.py::test_notify_wakes_relay_before_poll`,
   `test_harness_integration.py::test_service_fixtures_answer[minio]`,
   `test_typeahead.py::test_typeahead_latency_on_load_fixture`, and a setup ERROR in
   `test_projects_api.py::test_new_project_gets_default_policy_row` (unraisable exception
   at setup). Recheck them in CI; if one persists, look at whether P2-07 touched it (the
   only projects change is the new `projects.api.check_code_location`).
3. `make check` (sandboxed; set SEMGREP_SETTINGS_FILE/LOG_FILE/VERSION_CACHE_PATH to
   literal paths under /tmp/claude-1002, not `$TMPDIR`, or the hook refuses it). Last
   run: backend, daemon and profiles all green; one Vitest failure,
   `ProjectList.test.tsx` findByRole timeout (frontend untouched by P2-07; load flake;
   rerun).
4. `make test` bare (contract layer: generated runner tests include the new fixtures).
5. Update `daemon/README.md` (layout: outbox.py, worktree.py, launchd/; config keys
   agent_home, paths_dropin, kill_grace_s, outbox_max_bytes; protocol 2 behavior) and add
   `runs.profile_version` to Part A of the plan if the coordinator wants the shared name
   recorded there.
6. Commit, push `HEAD:wp/P2-07`, open the PR (`[P2-07] impl: Runner daemon v2`, body file
   in the scratchpad, ends with the Claude Code line), comment `@coderabbitai review`, run
   the review loop. Delete this file in a chore commit when done.

## Design as built (differences from the first handoff's plan are marked)

- Backend `protocol.py`: `SERVER_PROTOCOL_VERSIONS = (1, 2)`, `PROTOCOL_2_CAPABILITIES`,
  `negotiate(versions, capabilities=())`; models Stream, Status, UploadArtifact, Cancel,
  Archive, Nack (v1), AckBatch (ack v2), RunV2(Run), ResultV2(Result) with upgraders
  ack/run/result v1 -> v2; parse by (type, schema_version) dicts (`_DAEMON`, `_SERVER`);
  `DaemonMessage`/`ServerMessage` are plain unions now. Heartbeat `outbox_depth` with
  `exclude_if` (pydantic >= 2.12; pinned 2.13.5).
- `ws.py`: session `protocol`; `_ack` batches on protocol 2 (50 ids / 500 ms timer task);
  `_nack`; `_render` (run -> v2 on protocol 2, v1 with `workdir_policy: none` and
  timeout <= 3600 on protocol 1; cancel/archive never on protocol 1); `_acked(ids)`
  takes Ack and AckBatch; Stream/Status/UploadArtifact go to `_run_report` (not copied
  into runner_messages): artifact rule -> nack; run not dispatched here -> nack
  `unknown_run`; else run event (log|tool_call|file|status|artifact, payload = message
  minus schema_version/sent_at) + NOTIFY; Status started with profile_version updates
  `runs.profile_version` and `agent_profiles.profile_version` (profile of this runner).
  Result for a run already `cancelled` (and not itself cancelled) is acked and dropped.
- `rules.py`: `artifact_refusal` (media type, NUL/lone surrogate, 256 KiB, size/sha),
  `artifact_bytes`; unit test `tests/unit/test_artifact_rules.py`.
- `hermes.py`: dispatch writes RunV2 with `workdir_policy(packet)`; cancel as above;
  `_STREAMED` widened. `capabilities()` unchanged (locked `test_capabilities_are_phase_1`).
  `port.RunEvent.kind` widened. `workflows._outcome` maps `cancelled`;
  `api.RunOutcome.status` adds `cancelled`; `api.run_log(s, run_id)`.
- `packet_builder.py`: PathLocation, RepoLocation, `code_location`, `code_location_of`,
  `workdir_policy`; the conflict check goes through a NEW small public function
  `projects.api.check_code_location` (shared-file edit in the projects module: mypy strict
  forbids using the implicitly re-exported `validate_code_location`).
- Daemon: `config.py` (agent_home, paths_dropin, kill_grace_s, outbox_max_bytes),
  `protocol.py` (copies, `CAPABILITIES`, `PROTOCOL_2_ONLY`, builders make_stream,
  make_status, make_artifact, make_ack_batch, `code_location_of`), `outbox.py` (SQLite
  WAL; gap merge re-ids the merged gap so a server copy of the old one is never stale;
  `__del__` closes the connection so tests that never call close() raise no
  ResourceWarning), `state.py` (render per protocol; protocol-2-only messages are not
  kept on a protocol-1 session; replay drops them; `claim_run` via seen-set; cancel
  switches), `runner.py` (`KILL_GRACE_S`, `execute(..., cancel, kill_grace_s, workdir,
  on_record)` returns ResultV2, `_supervise`, stream lines, `touched_files` via `git
  status --porcelain -z`, status started/cancelling, worktree removed in `finally`),
  `worktree.py` (prepare/remove/cleanup, ReadWritePaths drop-in check after resolving
  symlinks, `git -c protocol.ext.allow=never`, `--` before the clone URL, branch name
  check), `main.py` (root refusal prints to stderr before logging, cleanup before dial,
  AckBatch acks on protocol 2, Cancel/Nack/AckBatch/Archive handling, MAX_FRAME 2 MiB),
  `systemd/tumnis-daemon.service` (plan's unit verbatim plus a comment block),
  `launchd/com.tumnis.daemon.plist` (new, per-user LaunchAgent).
- Daemon contract test: new `test_protocol2_messages_match_committed_schemas`.

## Deviations (keep all in the PR body)

- Server file is `agents/ws.py` (exists), not the plan's `runner_ws.py`.
- Batched ack is `ack` schema version 2 (`message_ids`) with an upgrader from v1, not a v1
  field: a new default field on `Ack` v1 would break T-P1-04-01's round trip.
- Protocol-2 negotiation is gated on the protocol-2 capabilities (coordinator: keep and
  list it), so locked T-P1-04-20 (`[1, 2]` -> 1 for a register with v1 capabilities)
  stays green. The plan says "highest common version".
- Code location read from `body.project.code_location` (P2-02's schema); the run view API
  (`GET /v1/runs/{id}/events`) is P2-04's, so T-15 reads `agents.api.run_log`.
- T-15's stub is `daemon/tests/stubs/hermes` with `three_lines.jsonl` (plan names
  `daemon/tests/stub_hermes.py`); the daemon runs as `python -m tumnis_daemon.main`.
- Artifact "binary" (NUL bytes) is refused `not_utf8`; a non-text media type is
  `bad_media_type`.
- `agents_0002` re-adds the kind check NOT VALID without VALIDATE (squawk).
- `projects.api.check_code_location` added (see above).
- The daemon acks each server message with its own one-id `AckBatch` on protocol 2 (no
  daemon-side batching timer); the server batches.
- On a protocol-1 session the daemon neither keeps nor sends stream/status/artifact, and a
  v2 `cancelled` result is rendered as a v1 `failed` result with error `cancelled`.
- The plan's "Refactor: one Transport interface in the daemon" is covered by the
  `Sender` protocol in `state.py` (the in-memory `MemoryLink` swaps in for the socket).

## Scott items

- Done checklist: "A real run on the Hermes VM streams to the run view", the
  `systemd-analyze security` score, "daemon runs as tumnis-agent" and the Mac plist
  install need the Hermes VM / a Mac.
- T-P1-04-20 (locked) assumed a v1-only server; kept green by capability-gated
  negotiation. If Scott prefers plain "highest shared version", that is a spec-change PR.
- `test_capabilities_are_phase_1` (locked) keeps `supports_stream/cancel` False for the
  daemon transport even though protocol 2 streams and cancels.
- The red commit's T-P2-07-16[1] was not red (it checks P1-04's unchanged protocol-1
  acks); CI on `a080719` would show it as a strict XPASS.

## Docs checked (cite in the PR body)

- Context7 `/pydantic/pydantic`: `Field(exclude_if=...)`, added in v2.12 (pinned 2.13.5).
- Context7 `/websites/websockets_readthedocs_io_en_stable`: `connect` as an async iterator
  reconnects with backoff on transient errors; fatal errors raise (pinned 17.1).
- Context7 `/dbos-inc/dbos-docs`: `DBOS.send`/`send_async` `idempotency_key` for
  exactly-once delivery from outside a workflow (pinned dbos 3.1.0).
- Plan links: git-worktree, systemd.exec, launchd jobs (first-party). Not yet fetched on
  the web; do so for the unit directives if CodeRabbit questions one.

## Verify

```bash
cd daemon && uv run pytest -q && uv run ruff check . && uv run mypy
cd daemon && CI=1 uv run pytest -q tests/test_replay_property.py
cd backend && uv run pytest -q -m contract tumnis/modules/agents/tests/contract
cd backend && uv run pytest -q -m "not integration and not contract" tumnis/modules/agents
make test-int    # bare, from the worktree root
```
