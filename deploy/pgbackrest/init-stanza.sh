#!/usr/bin/env bash
# Stanza setup (P0-28, REL-1): create the `tumnis` stanza in both repositories (idempotent),
# prove WAL archiving end to end with `check`, and take a first full backup in any
# repository that has none, so the RPO is covered from the first start. The `backup`
# service runs this, then supercronic with the crontab.
set -euo pipefail

for _ in $(seq 60); do
  pg_isready -q -h /var/run/postgresql -U postgres && break
  sleep 2
done
pg_isready -h /var/run/postgresql -U postgres

pgbackrest --stanza=tumnis stanza-create
pgbackrest --stanza=tumnis check

for repo in 1 2; do
  if ! pgbackrest --stanza=tumnis --repo="$repo" info --output=json \
    | grep -Eq '"type" *: *"full"'; then
    echo "init-stanza: repo $repo has no full backup yet; taking one"
    run-backup.sh "$repo" full
  fi
done
