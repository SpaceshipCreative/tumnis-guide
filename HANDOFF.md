# HANDOFF: PR #52 (wp/P1-14, storage: interface, server path and S3)

Paused by Scott on 2026-09-29. Do not merge; the coordinator merges.

## State

- Branch head before this note: `ee651cc`. main was merged in at `4c47412`. `alembic heads` shows one head per branch (knowledge_0003).
- PR is MERGEABLE (no conflicts).
- No CodeRabbit review has arrived yet for `ee651cc` (re-review requested at about 18:50Z).

## CodeRabbit threads (all resolved)

Round 1 (review on b10287d):
| Thread | Issue | Red test | Fix |
| --- | --- | --- | --- |
| PRRT_kwDOUx-vCc6nLbnn | server_path TOCTOU (parent swapped for a symlink) | 5b2c7d3 | 1b31784 (all ops by folder fd; no-link contract class added) |
| PRRT_kwDOUx-vCc6nLbn0 | server_path roots in hosted mode | 6d26145 | 9fde015 (hosted refuses server_path at create and open, FR-15.7) |
| PRRT_kwDOUx-vCc6nLbn8 | plain-http S3 in hosted mode | 1e05fab | 86da99c (checked after the SSRF guard so T-P1-14-13 keeps `ssrf_blocked`) |
| PRRT_kwDOUx-vCc6nLboD | sha256 compared with the S3 ETag | 898d862 | d90c613 (`_holds` compares bytes) |

Round 2 (review on d90c613):
| Thread | Issue | Red test | Fix |
| --- | --- | --- | --- |
| PRRT_kwDOUx-vCc6nPUb1 | aioboto3 client outside tumnis.core.net | none | Declined with reason; **Scott decides** (see below) |
| PRRT_kwDOUx-vCc6nPUb8 | partial file after a failed no-link create | 04dfd4c | c6b6498 |
| PRRT_kwDOUx-vCc6nPUcO | queued writes to one path did not chain etags | (commit before fff5297) | fff5297 |
| PRRT_kwDOUx-vCc6nPUcd | folderless project could never get a folder | (commit before 9ac26dd) | 9ac26dd |
| PRRT_kwDOUx-vCc6nPUcm | S3 form sent no region | (commit before ee651cc) | ee651cc (path-style toggle not added; reason in the thread) |

Other: af1716c adds a `.gitleaks.toml` allow-list for the bare `IfNoneMatch=`/`IfMatch=` names (rule `generic-api-key` only). gitleaks flagged `IfNoneMatch="*"` from cfbde94, and the security job had failed since b10287d.

## CI on ee651cc (run `ci`)

Success: lint, unit, integration, e2e, security, spec-guard, red-proof, traceability, performance, skills; version-skew success.
**contract: cancelled** (18:53 to 18:55Z; no failed step; cause unknown). On d90c613, contract passed. Re-run it with `gh run rerun <id> --failed` or push again.
preview: queued (no homelab runner; ignore).

## Next steps

1. Re-run the cancelled contract job and confirm it goes green.
2. Wait for CodeRabbit's review of the latest head (up to 20 min; the quota may be exhausted). Handle any new threads with the same TDD loop.

## Scott decides

- Whether to list the aioboto3 S3 client as a named exception in AGENTS.md's `tumnis.core.net` rule, next to the Jev SDK. It resolves every name through `resolve_and_check` via `_GuardedResolver`.
- Confirm the `unicodedata` addition to the rules allow-list.
- Whether storage calls may run in the api process (AGENTS rule: no network from the api). Location create, test, save_note and set_project_location run S3 and disk I/O inline today.

## Gotchas (this VM)

- Commit identity: `git -c user.name=Claude -c user.email=noreply@anthropic.com commit ...`; never as Scott.
- `make check` needs `SEMGREP_SETTINGS_FILE=$TMPDIR/semgrep-settings.yml` (the home dir is read-only under the sandbox).
- Docker-backed tests need the sandbox off. Use `-n 6`, not `-n auto`.
- `gh pr checks` returns 403; use `gh run list --branch wp/P1-14`.
