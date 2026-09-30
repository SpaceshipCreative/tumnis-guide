# HANDOFF: P2-07 Runner daemon v2

Stopped on the coordinator's "HANDOFF NOW" (context watcher). Clean point: the red spec
tests are committed and `make check` passes. No implementation code yet. No PR yet.

## State

- Branch: `wp/P2-07` (pushed as `HEAD:wp/P2-07`), based on main `4bd82f5`.
- Commits:
  - `a080719` test(agents): P2-07 spec tests (red)
  - this handoff commit (`chore: P2-07 handoff`)
- PR: none opened yet. CI: not run yet (the push of the red commit will trigger it).
- Review threads: none.
- `make check` (with SEMGREP_* under $TMPDIR, `PYTEST_XDIST_AUTO_NUM_WORKERS=3`): green,
  backend 1018 passed; daemon 17 passed, 9 xfailed.
- Red confirmed locally: all 9 daemon spec tests (xfail, right reasons: missing
  `outbox`/`KILL_GRACE_S`/config fields, unit lacks 17 directives, no stderr message) and
  the backend contract ones (missing schemas, `SERVER_PROTOCOL_VERSIONS == (1,)`, missing
  packet_builder functions). The integration ones (T-02, 04, 05, 12, 16, 17 in
  `test_runner_dedupe.py`; T-15 in `backend/tests/integration/test_daemon_e2e.py`) were
  NOT run locally (Docker layer): run `make test-int` or rely on CI to confirm they xfail.

## Spec tests (all strict xfail `spec:P2-07`)

| ID | File |
| --- | --- |
| 01, 11 | `backend/tumnis/modules/agents/tests/contract/test_runner_protocol_v2.py` |
| 13 | `backend/tumnis/modules/agents/tests/contract/test_golden_packets.py` |
| 02, 04, 05, 12, 16, 17 | `backend/tumnis/modules/agents/tests/integration/test_runner_dedupe.py` |
| 15 | `backend/tests/integration/test_daemon_e2e.py` (new folder, has `__init__.py`) |
| 03 | `daemon/tests/test_replay_property.py` |
| 06 | `daemon/tests/test_cancel.py` |
| 07, 10 | `daemon/tests/test_security.py` |
| 08, 09, 18 | `daemon/tests/test_worktree.py` |
| 14 | `daemon/tests/test_outbox.py` |

Temporary `# type: ignore[...]` in two red tests (remove when the code lands, mypy's
warn_unused_ignores will insist): `test_golden_packets.py` (packet_builder import) and
`test_runner_protocol_v2.py` (two `negotiate(..., capabilities)` calls). Removing an ignore
is not an assertion change.

## Design the tests pin (implement to this)

### Backend `agents/protocol.py`
- `SERVER_PROTOCOL_VERSIONS = (1, 2)`; `PROTOCOL_2_CAPABILITIES = {"stream", "cancel",
  "upload_artifact"}`; `negotiate(versions, capabilities=())` picks 2 only when the
  register lists 2 AND those capabilities. This keeps the locked P1-04 test T-P1-04-20
  green (`[1,2]` with capabilities `["run","health"]` -> 1; `[2]` alone -> error).
  Register's `capabilities` Literal grows: `stream`, `cancel`, `upload_artifact`,
  `worktree`, `archive` (additive to v1 schema).
- New v1 types: `Stream` (run_id, seq>=1, kind log|tool_call|file_touched, text max 8192,
  ts), `Status` (run_id|None, profile|None (NAME_RE), profile_version|None max 64, state
  started|waiting|cancelling|gap, detail|None), `Cancel` (run_id, reason), `UploadArtifact`
  (run_id, name, media_type: str, size: int, sha256: str, content: str; deliberately
  loose so the server answers `nack` codes, not `invalid_message`), `Archive` (profile,
  archive_id: str; behavior P2-18), `Nack` (nack_of, code too_large|bad_media_type|
  not_utf8|sha_mismatch|unknown_run, detail|None).
- Version 2: `AckBatch` = `@versioned("runner","ack",2)` with `message_ids` (1..50),
  `RunV2(Run)` (schema_version 2, timeout_s le=86_400), `ResultV2(Result)` (status adds
  `cancelled`). Register `@upgrader("runner", "ack"|"run"|"result", 1)` so
  `parse_versioned` still accepts the v1 fixtures (generated contract tests).
- `_parse` picks the model by (type, schema_version); known type + unknown version ->
  `unsupported_schema_version` (T-P1-04-02 needs heartbeat v2 refused that way).
- Heartbeat "plus outbox depth": `outbox_depth: int | None = Field(None,
  exclude_if=lambda v: v is None)` keeps T-P1-04-01's round trip (pydantic 2.13 has
  exclude_if). Only send it on protocol-2 sessions.
- Fixtures (committed): `backend/tests/contract/fixtures/runner/{stream,status,cancel,
  upload_artifact,archive,nack}/v1.json`, `ack/v2.json`, `run/v2.json`, `result/v2.json`.
  Then `make gen` (schemas/runner/v1 + v2, generated contract tests) and commit.

### Backend `agents/ws.py` (plan says `runner_ws.py`; the file is `ws.py`)
- Session protocol from negotiate; `registered.protocol_version`.
- Acks: protocol 1 -> `Ack{ack_of}` per message (unchanged); protocol 2 -> batcher
  (flush at 50 ids or 500 ms); nack immediate. Accept both `Ack` and `AckBatch` from the
  daemon for mailbox rows.
- Forwarder renders mailbox payloads per session: protocol 2 -> `run` as schema_version 2;
  protocol 1 -> v1 (`workdir_policy` none, timeout <= 3600); skip `cancel`/`archive` on v1.
- Stream -> `run_events` (kind log|tool_call|file for file_touched; payload has seq, text,
  ts), ON CONFLICT DO NOTHING, ack after commit; run must have been dispatched to this
  runner (mailbox row uuid5(run_id,"run")) else `nack unknown_run`. Don't copy stream,
  artifact, status into `runner_messages`.
- UploadArtifact: codes via a pure rule in rules.py (sha computed in ws.py; hashlib is not
  allowed in rules): media type not in {text/plain, text/markdown, text/x-diff,
  application/json} -> bad_media_type; content with NUL or not encodable as UTF-8 (lone
  surrogates) -> not_utf8; > 256 KiB -> too_large; size or sha mismatch -> sha_mismatch;
  unknown run -> unknown_run. Else `run_events` kind `artifact`.
- Status -> run event kind `status`; state `started` with profile_version sets
  `runs.profile_version` and `agent_profiles.profile_version` (by profile name).
- Result (v1 or v2): if the run is already `cancelled` (v1 fallback) ack and drop (no
  event, no workflow send). `RunOutcome.status` gains `cancelled`; `_outcome` maps it.
- Migration `agents_0002` (branch agents, `--head agents@head`, phase expand): add
  `runs.profile_version text`; widen `ck_run_events_kind` with `artifact`, `status`
  (drop + add NOT VALID + VALIDATE for squawk). Mirror in models.py. Report the id.
  (P2-04 plans runs.profile_version in its own migration: flag for the coordinator.)

### Backend other
- `packet_builder.py`: `PathLocation`, `RepoLocation`, `CodeLocation`,
  `code_location(code_path, repo_url, default_branch=None)` (uses
  `projects.api.validate_code_location`; conflict raises its ValueError with
  `code == "code_location_conflict"`), `code_location_of(packet)` (reads
  `body.project.code_location`, P2-02's place), `workdir_policy(packet)`.
- `adapters/hermes.py`: `DaemonTransport.dispatch` sets `workdir_policy` from the packet;
  `DaemonTransport.cancel`: no-op when the run has a terminal event/status; runner on
  protocol >= 2 -> queue `cancel` mailbox row (uuid5(run_id,"cancel")) + NOTIFY; protocol
  1 -> mark run `cancelled`, error "stop requested, runner is an older version", `failed`
  run event, DBOS.send {"status":"cancelled"} to its workflow if any (best effort).
  Keep `capabilities()` supports_stream/cancel False (locked contract test
  `test_capabilities_are_phase_1`). `RunEvent.kind` and `_STREAMED` add log, tool_call,
  file (plus artifact, status).
- `api.run_log(s, run_id) -> list[RunEvent]`: the run view seam T-15 uses (P2-04 owns the
  route `GET /v1/runs/{id}/events`); stream lines ordered by seq.

### Daemon
- `config.py`: `agent_home=/home/tumnis-agent`, `paths_dropin=/etc/systemd/system/
  tumnis-daemon.service.d/paths.conf`, `kill_grace_s=10.0`, `outbox_max_bytes=50 MiB`;
  `load_config` reads them (T-15 writes `agent_home` in its TOML).
- `protocol.py`: copies of every model above; `PROTOCOL_VERSIONS=(1,2)`; capabilities
  run, health, stream, cancel, upload_artifact, worktree (not archive until P2-18);
  builders `make_stream(run_id, corr, seq, kind, text)`, `make_artifact(run_id, corr,
  name, media_type, content)` (size, sha256), `ResultV2`, `envelope`; `PathLocation`,
  `RepoLocation`; parse by (type, version).
- `outbox.py`: `Outbox(path, max_bytes)` SQLite (WAL, synchronous NORMAL): `put(dict)`,
  `unacked()` in insertion order, `ack(ids)`, `seen_command`, `mark_command`, `close`,
  `depth`. Full: drop oldest `stream` kind `log` rows, one merged `status` gap per run with
  detail exactly `gap: {n} lines (seq {a}-{b})`, inserted at the first dropped row's id;
  never drop anything else; total bytes <= max when droppables allow.
- `state.py`: `StateStore(state_dir, *, outbox_max_bytes=...)` backed by the Outbox
  (`state.db`); keep `send_reliably`, `ack`, `unacked() -> list[str]`, `replay_unacked`,
  `claim_run` (use seen-set, survives restart), add `acked(ids)`, `close()`,
  `cancel(run_id, reason) -> bool`; render frames per `protocol_version` at send time
  (v2 -> result schema 2; v1 -> drop stream/status/artifact, result v1).
- `runner.py`: `KILL_GRACE_S = 10.0`; `execute(msg, cfg, *, timeout_s=None, cancel=None,
  kill_grace_s=None, workdir=None, on_record=None)`; cancel -> SIGTERM group, wait
  grace, SIGKILL, `ResultV2(status="cancelled")`. `run_skill`: status started (profile
  VERSION from `agent_home/.hermes/profiles/<p>/VERSION`), stream lines (assistant ->
  log, tool_call -> tool_call, `git status --porcelain` after tool lines -> file_touched),
  worktree when `workdir_policy == "worktree"` (query file in `<wt>/.tumnis/`, added to
  info/exclude), status cancelling on cancel, remove the worktree after the result is in
  the outbox. Don't touch `read_recording` (P1-05 edits it).
- `worktree.py`: `prepare(cfg, run_id, loc) -> Path` (`<state_dir>/worktrees/<id>`,
  mirrors at `<state_dir>/repos/<sha256(url)>.git`), `remove(cfg, run_id)`,
  `cleanup_stale_worktrees(cfg) -> list[str]`, `WorktreeRefused` (message names the path
  and "ReadWritePaths"); path must resolve under agent_home or a ReadWritePaths entry of
  the drop-in; symlinks resolved first.
- `main.py`: `refuse_root()` prints "refusing to run as root" to stderr, exit 78;
  `main` calls `cleanup_stale_worktrees` before `connect`; reconnect loop replays the
  outbox; handle Cancel/Nack/AckBatch; protocol-2 acks as `AckBatch`.
- `systemd/tumnis-daemon.service`: the plan's unit verbatim (Restart=on-failure etc.).
  `launchd/com.tumnis.daemon.plist`: new (KeepAlive, RunAtLoad, logged-in user).
- CI: `daemon` job has `timeout-minutes: 3`; the property test runs 500 examples when CI
  is set; raise the timeout if needed. The backend integration job needs nothing new
  (T-15 runs the daemon with `PYTHONPATH=daemon` on the backend venv; websockets 17.1 and
  pydantic 2.13.5 are pinned in both).

## Remaining steps

1. Implement in the plan's TDD order, removing one marker at a time only after that test
   has run and passed (Scott's standing approval): 01+11, 07+10, 14 then 03, 04/05/02,
   06/12, 13/08/09/18, 16/17, 15. `make gen` after the protocol models.
2. `make check`; `make test`; `make test-int` (bare, from the worktree root); daemon
   `uv run pytest`.
3. README/AGENTS notes if needed; open the PR (`[P2-07] impl: Runner daemon v2`), comment
   `@coderabbitai review`, run the review loop.

## Deviations so far

- Server file is `agents/ws.py` (exists), not the plan's `runner_ws.py`.
- Batched ack is `ack` schema version 2 (`message_ids`) with an upgrader from v1, not a v1
  field: a new default field on `Ack` v1 would break T-P1-04-01's round trip.
- Protocol-2 negotiation is gated on the protocol-2 capabilities, so locked T-P1-04-20
  (`[1, 2]` -> 1 for a register with v1 capabilities) stays green.
- Code location read from `body.project.code_location` (P2-02's schema); the plan's run
  view API (`GET /v1/runs/{id}/events`) is P2-04's, so T-15 reads `agents.api.run_log`.
- T-15's stub is `daemon/tests/stubs/hermes` with `three_lines.jsonl` (plan names
  `daemon/tests/stub_hermes.py`); the daemon runs as `python -m tumnis_daemon.main`.
- Artifact "binary" (NUL bytes) is refused `not_utf8`; a non-text media type is
  `bad_media_type`.

## Scott items

- Done checklist: "A real run on the Hermes VM streams to the run view", the
  `systemd-analyze security` score, and the Mac plist install need the Hermes VM / a Mac.
- T-P1-04-20 (locked) assumed a v1-only server; kept green by capability-gated
  negotiation. If Scott prefers plain "highest shared version", that is a spec-change PR.
- `test_capabilities_are_phase_1` (locked) keeps `supports_stream/cancel` False for the
  daemon transport even though protocol 2 streams and cancels.

## Verify

```bash
bash make-check-with-tmp-semgrep   # SEMGREP_SETTINGS_FILE/LOG_FILE/VERSION_CACHE_PATH under $TMPDIR
cd daemon && uv run pytest -q
cd backend && uv run pytest -q -m contract tumnis/modules/agents/tests/contract
make test-int    # bare, from the worktree root
```
