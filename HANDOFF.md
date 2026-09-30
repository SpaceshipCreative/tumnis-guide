# HANDOFF: fix/review-followups-1 (PR #96)

PR: https://github.com/SpaceshipCreative/tumnis-guide/pull/96
Title: "fix: review follow-ups (daemon health: address parse, .env quoting, probe exceptions; logout on 401; AgentTools list key)"
Branch: `fix/review-followups-1` (push with `/usr/bin/git push origin HEAD:fix/review-followups-1`)

## State when stopped (coordinator STOP: the VM ran out of RAM)

The PR was merge-ready at `d37733d18c6b980ef76f0a314392fa97a7606269`, before this HANDOFF commit:
- CI was green on d37733d: GitGuardian, skills, spec-guard, contract, daemon, e2e, unit, performance, version-skew, traceability, lint, integration, security and red-proof all passed. `preview` was pending, which is expected (no homelab runner).
- CodeRabbit reviewed d37733d (check `CodeRabbit: pass`). Its summary comment said "No actionable comments were generated". There are 0 inline comments and 0 review threads.
- The HANDOFF commit on top of d37733d re-triggers CI. It changes only this file.

## Commits

- 49bd740 fix(daemon): health report degrades on odd .env input instead of crashing (findings 1, 4, 5)
- bba5c54 fix(frontend): a 401 on sign out still clears the cache and opens sign-in (finding 2)
- d37733d test(daemon): a raising probe sends nothing elsewhere (decision 18)
- (this) docs: HANDOFF.md

## Findings

1. health.py `ip_address` ValueError: real, but raised elsewhere. The crash came from getaddrinfo (UnicodeError) and httpx.InvalidURL. Fixed in `_origin` and `_resolve`.
2. AppHeader: logout returning 401: real, fixed.
3. AgentTools list key: not real, no code. The reasons are in the PR body.
4. `.env` quoted value followed by a comment: real. `.env` is now read with python-dotenv's grammar, which is what Hermes uses.
5. A non-HTTPError from a probe aborted the report: real. Now only that kind reports "check failed".

## Next steps

1. Check the new head's CI with `gh pr checks 96`; wait for everything except `preview` to pass.
2. Check CodeRabbit. It may review the HANDOFF commit. It is out of included reviews ("used all 10"), so it may not.
3. Resolve or answer any new threads (see ~/tumnis-coordinator/pr-review-loop.md).
4. Send "#96 MERGE-READY at <sha>" to main.
5. The coordinator may want HANDOFF.md dropped from the branch before merge, with a follow-up commit that deletes it.

## Local notes

- `make check` needs the semgrep env vars pointing into `/tmp/claude-1002/review-followups-1/`, because ~/.semgrep is read-only.
- The daemon's pytest has no xdist, so run it without `-n`.
