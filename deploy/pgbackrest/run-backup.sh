#!/usr/bin/env bash
# One pgBackRest backup, recorded in ops_backup_runs (P0-28, REL-1). The backup freshness
# check reads that table; a run that cannot be recorded counts as missing, never as fresh.
#
#   run-backup.sh REPO TYPE        # REPO 1|2, TYPE full|diff|incr
#
# Runs as postgres in the `backup` service, beside the database: PGDATA and the socket are
# shared volumes, psql uses peer authentication on the socket. Exits with the backup's result.
set -uo pipefail

repo="${1:-}"
type="${2:-}"
case "$repo" in 1 | 2) ;; *) echo "usage: $0 1|2 full|diff|incr" >&2; exit 2 ;; esac
case "$type" in full | diff | incr) ;; *) echo "usage: $0 1|2 full|diff|incr" >&2; exit 2 ;; esac

started="$(date -u +%FT%TZ)"
if pgbackrest --stanza=tumnis --repo="$repo" --type="$type" backup; then ok=true; else ok=false; fi

if ! psql -X -q -v ON_ERROR_STOP=1 -U postgres -d tumnis \
  -v repo="$repo" -v type="$type" -v started="$started" -v ok="$ok" <<'SQL'
INSERT INTO ops_backup_runs (repo, type, started_at, finished_at, ok)
VALUES (:'repo', :'type', :'started', now(), :'ok');
SQL
then
  echo "run-backup: could not record repo $repo $type (ok=$ok) in ops_backup_runs" >&2
fi
[ "$ok" = true ]
