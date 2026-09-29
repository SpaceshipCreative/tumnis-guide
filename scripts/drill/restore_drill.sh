#!/usr/bin/env bash
# The restore drill (P0-28, A0.4, REL-1): a point-in-time restore from pgBackRest repo 2
# must meet the 15-minute RPO and the 1-hour RTO. The drill is the test.
#
#   scripts/drill/restore_drill.sh --mode prod        # quarterly, homelab, the B2 repository
#   scripts/drill/restore_drill.sh --mode rehearsal   # nightly and locally, MinIO in compose.test
#
# Steps (numbered as in the plan): 1 preflight, 2 marker, 3 fence, 4 wait for the archive
# (RPO), 5 loss, 6 restore (the RTO clock starts), 7 scratch server with archiving off,
# 8 checks, 9 stop the clock, 10 `tumnis drill record`. Any failed step exits non-zero.
#
# --mode prod never touches the production containers: it reads from them (docker exec) and
# restores into a new volume and a scratch container on a drill network. It needs:
#   DRILL_SOURCE_PG    the running production Postgres container (name or id)
#   DRILL_SOURCE_APP   a running api or worker container of the same release
#   DRILL_PGBACKREST_CONF_D  host directory with secrets.conf (default /etc/pgbackrest/conf.d)
# --mode rehearsal brings up deploy/compose.test.yaml as project tumnis-drill (api on
# 127.0.0.1:${TUMNIS_TEST_PORT:-18431}), destroys its Postgres container and volume, restores,
# and removes the whole stack at the end (DRILL_KEEP_STACK=1 keeps it for debugging).
# Host requirements: bash, docker (with compose), jq.
set -euo pipefail

usage() { echo "usage: $0 --mode prod|rehearsal" >&2; }

MODE=prod
while [ "$#" -gt 0 ]; do
  case "$1" in
    --mode) MODE="${2:-}"; shift 2 ;;
    --mode=*) MODE="${1#*=}"; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done
case "$MODE" in prod | rehearsal) ;; *) usage; exit 2 ;; esac

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RPO_LIMIT_S=900   # 15 minutes (REL-1); also enforced by `tumnis drill record` (core/drill.py)
RTO_LIMIT_S=3600  # 1 hour (REL-1)
ARCHIVE_WAIT_S="${DRILL_ARCHIVE_WAIT_S:-900}"
SCRATCH=tumnis-drill-pg
SCRATCH_VOLUME=tumnis-drill-pgdata
SCRATCH_PORT=5433
PGDATA_PATH=/var/lib/postgresql/18/docker

log() { printf '%s drill[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$MODE" "$*" >&2; }
fail() {
  log "FAILED: $*"
  exit 1
}
need() { command -v "$1" >/dev/null || fail "preflight: $1 is not installed"; }

# --- Mode-specific wiring -------------------------------------------------------------
if [ "$MODE" = rehearsal ]; then
  PROJECT="${DRILL_PROJECT:-tumnis-drill}"
  export TUMNIS_TEST_PORT="${TUMNIS_TEST_PORT:-18431}"
  COMPOSE=(docker compose -p "$PROJECT" -f "$REPO_ROOT/deploy/compose.test.yaml")
  NETWORK="${PROJECT}_default"
  PGBR_ARGS=(
    --env-file "$REPO_ROOT/deploy/pgbackrest/rehearsal.env"
    -v "$REPO_ROOT/deploy/pgbackrest/test-conf.d:/etc/pgbackrest/conf.d:ro"
    -v "${PROJECT}_minio_certs:/etc/pgbackrest/minio:ro"
  )
else
  NETWORK=tumnis-drill-net
  PGBR_ARGS=(-v "${DRILL_PGBACKREST_CONF_D:-/etc/pgbackrest/conf.d}:/etc/pgbackrest/conf.d:ro")
fi

cleanup() {
  local status=$?
  set +e
  docker rm -f "$SCRATCH" >/dev/null 2>&1
  docker volume rm -f "$SCRATCH_VOLUME" >/dev/null 2>&1
  if [ "$MODE" = prod ]; then
    docker network rm "$NETWORK" >/dev/null 2>&1
  elif [ "${DRILL_KEEP_STACK:-0}" != 1 ]; then
    "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1
  fi
  if [ "$status" -eq 0 ]; then log "passed"; else log "exit $status"; fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

src_psql() { docker exec -u postgres "$SRC_PG" psql -X -At -v ON_ERROR_STOP=1 -d tumnis "$@"; }
scratch_psql() {
  docker exec -u postgres "$SCRATCH" psql -X -At -v ON_ERROR_STOP=1 -p "$SCRATCH_PORT" "$@"
}
repo2_info() { docker exec -u postgres "$SRC_PG" pgbackrest --stanza=tumnis --repo=2 info --output=json; }
has_full_and_archive() {
  jq -e '(.[0].backup // [] | map(select(.type == "full")) | length > 0)
    and (.[0].archive // [] | map(select(.min != null)) | length > 0)' >/dev/null
}

# --- 1. Preflight ---------------------------------------------------------------------
need docker
need jq
if [ "$MODE" = rehearsal ]; then
  log "1 preflight: starting a fresh compose.test stack as project $PROJECT"
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  # Only the long-running services the drill needs (their one-shot dependencies run
  # first); waiting on the whole stack races the seed container's exit.
  "${COMPOSE[@]}" up -d --wait --build api worker backup >&2
  SRC_PG="$("${COMPOSE[@]}" ps -q postgres)"
  SRC_APP="$("${COMPOSE[@]}" ps -q api)"
  # The backup service takes the first full backups right after start.
  for _ in $(seq 60); do
    repo2_info 2>/dev/null | has_full_and_archive && break
    sleep 5
  done
else
  SRC_PG="${DRILL_SOURCE_PG:?set DRILL_SOURCE_PG to the production Postgres container}"
  SRC_APP="${DRILL_SOURCE_APP:?set DRILL_SOURCE_APP to a production api or worker container}"
  docker network create "$NETWORK" >/dev/null
fi
[ -n "$SRC_PG" ] && [ -n "$SRC_APP" ] || fail "preflight: source containers not found"
repo2_info | has_full_and_archive || fail "preflight: repo2 has no full backup or no archive range"
PG_IMAGE="$(docker inspect -f '{{.Image}}' "$SRC_PG")"
APP_IMAGE="$(docker inspect -f '{{.Image}}' "$SRC_APP")"
docker image inspect "$PG_IMAGE" "$APP_IMAGE" >/dev/null || fail "preflight: release images missing"
log "1 preflight: repo2 has a full backup and an archive range; image $PG_IMAGE"

# --- 2. Marker --------------------------------------------------------------------------
MARKER_ID="$(src_psql -c "INSERT INTO ops_drill_markers (id, kind) VALUES (gen_random_uuid(), 'marker') RETURNING id" | head -1)"
# pgBackRest takes the target as "YYYY-MM-DD HH:MM:SS.ffffff+00"; so does `drill record`.
IFS='|' read -r T_MARKER MARKER_WAL < <(src_psql -F '|' -c \
  "SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US') || '+00', pg_walfile_name(pg_current_wal_lsn())")
log "2 marker $MARKER_ID at $T_MARKER (WAL $MARKER_WAL)"

# --- 3. Fence ---------------------------------------------------------------------------
sleep 2
FENCE_ID="$(src_psql -c "INSERT INTO ops_drill_markers (id, kind) VALUES (gen_random_uuid(), 'fence') RETURNING id" | head -1)"
FENCE_WAL="$(src_psql -c "SELECT pg_walfile_name(pg_current_wal_lsn())")"
log "3 fence $FENCE_ID (WAL $FENCE_WAL)"

# --- 4. Wait for the archive (RPO) ------------------------------------------------------
deadline=$(($(date +%s) + ARCHIVE_WAIT_S))
RPO_OBSERVED=""
while [ "$(date +%s)" -lt "$deadline" ]; do
  # A segment name (not a .backup or .history file) at or past the fence's segment.
  row="$(src_psql -F ' ' -c "SELECT (last_archived_wal = '$FENCE_WAL' OR substr(last_archived_wal, 1, 24) > '$FENCE_WAL'),
    ceil(extract(epoch FROM last_archived_time - '$T_MARKER'::timestamptz))::int FROM pg_stat_archiver")"
  if [ "${row%% *}" = t ]; then
    RPO_OBSERVED="${row##* }"
    break
  fi
  sleep 5
done
[ -n "$RPO_OBSERVED" ] || fail "4 RPO missed: $FENCE_WAL not archived within ${ARCHIVE_WAIT_S}s"
[ "$RPO_OBSERVED" -le "$RPO_LIMIT_S" ] || fail "4 RPO missed: ${RPO_OBSERVED}s > ${RPO_LIMIT_S}s"
log "4 archived through $FENCE_WAL; RPO observed ${RPO_OBSERVED}s"

# --- 5. Loss ----------------------------------------------------------------------------
if [ "$MODE" = rehearsal ]; then
  "${COMPOSE[@]}" rm -sf backup postgres >&2
  docker volume rm "${PROJECT}_pgdata" >/dev/null
  log "5 loss: Postgres container and its volume destroyed"
else
  log "5 loss: production untouched; restoring into a new volume with no access to it"
fi

# --- 6. Restore (the RTO clock starts) ---------------------------------------------------
START="$(date +%s)"
docker volume create "$SCRATCH_VOLUME" >/dev/null
docker run --rm -u root --network "$NETWORK" "${PGBR_ARGS[@]}" \
  -v "$SCRATCH_VOLUME:/var/lib/postgresql" --entrypoint /bin/sh "$PG_IMAGE" -c '
    install -d -o postgres -g postgres -m 0700 "$1"
    exec gosu postgres pgbackrest --stanza=tumnis --repo=2 --type=time "--target=$2" \
      --target-action=promote restore' drill "$PGDATA_PATH" "$T_MARKER" >&2 \
  || fail "6 pgbackrest restore failed"
log "6 restored to $T_MARKER"

# --- 7. Scratch server, archiving off -------------------------------------------------
# Never with archiving on: a restored cluster would push a new timeline into the stanza.
# The pgBackRest config and secrets are mounted read-only: restore_command (archive-get)
# replays WAL from repo2 up to the target.
docker run -d --name "$SCRATCH" --network "$NETWORK" "${PGBR_ARGS[@]}" \
  -v "$SCRATCH_VOLUME:/var/lib/postgresql" "$PG_IMAGE" \
  postgres -c config_file=/etc/postgresql/postgresql.conf -c archive_mode=off \
  -c port="$SCRATCH_PORT" >/dev/null
promoted=""
while [ $(($(date +%s) - START)) -lt "$RTO_LIMIT_S" ]; do
  if docker exec "$SCRATCH" pg_isready -q -h /var/run/postgresql -p "$SCRATCH_PORT" 2>/dev/null \
    && [ "$(scratch_psql -d postgres -c 'SELECT pg_is_in_recovery()' 2>/dev/null)" = f ]; then
    promoted=yes
    break
  fi
  [ "$(docker inspect -f '{{.State.Running}}' "$SCRATCH")" = true ] \
    || { docker logs --tail 40 "$SCRATCH" >&2; fail "7 scratch server exited"; }
  sleep 2
done
[ -n "$promoted" ] || fail "7 scratch server not promoted within the RTO"
log "7 scratch server promoted (archive_mode=off)"

# --- 8. Checks --------------------------------------------------------------------------
[ "$(scratch_psql -d tumnis -c "SELECT count(*) FROM ops_drill_markers WHERE id = '$MARKER_ID'")" = 1 ] \
  || fail "8 marker row missing after the restore"
[ "$(scratch_psql -d tumnis -c "SELECT count(*) FROM ops_drill_markers WHERE id = '$FENCE_ID'")" = 0 ] \
  || fail "8 fence row present: recovery overshot the target"
[ "$(scratch_psql -d tumnis -c 'SELECT count(*) > 0 FROM deployment_marker')" = t ] \
  || fail "8 restored database has no rows in deployment_marker"
[ "$(scratch_psql -d tumnis_dbos -c "SELECT to_regclass('dbos.workflow_status') IS NOT NULL")" = t ] \
  || fail "8 DBOS system tables missing"

# The scratch copy is throwaway: give both roles a one-off password so the release's own
# CLI can check it over TLS, like the app does.
DRILL_PW="$(docker exec "$SCRATCH" openssl rand -hex 16)"
scratch_psql -d postgres -q -c "ALTER ROLE tumnis_owner PASSWORD '$DRILL_PW'" \
  -c "ALTER ROLE tumnis_app PASSWORD '$DRILL_PW'"
SCRATCH_URL="$SCRATCH:$SCRATCH_PORT/tumnis?sslmode=require"
tumnis_on_scratch() {
  DATABASE_URL="postgresql+psycopg://tumnis_app:$DRILL_PW@$SCRATCH_URL" \
    DATABASE_DIRECT_URL="postgresql+psycopg://tumnis_app:$DRILL_PW@$SCRATCH_URL" \
    DATABASE_OWNER_URL="postgresql+psycopg://tumnis_owner:$DRILL_PW@$SCRATCH_URL" \
    docker run --rm --network "$NETWORK" -e DATABASE_URL -e DATABASE_DIRECT_URL \
    -e DATABASE_OWNER_URL "$APP_IMAGE" tumnis "$@"
}
tumnis_on_scratch migrate --check >&2 || fail "8 tumnis migrate --check: not at head"
if docker run --rm "$APP_IMAGE" tumnis --help | grep -qE '^\W*audit\b'; then
  tumnis_on_scratch audit verify >&2 || fail "8 tumnis audit verify failed"
else
  log "8 tumnis audit verify: not in this release yet (P0-15); skipped"
fi
log "8 checks passed: marker present, fence absent, migrations at head, DBOS tables present"

# --- 9. Stop the clock --------------------------------------------------------------------
RTO="$(($(date +%s) - START))"
log "9 RTO ${RTO}s (limit ${RTO_LIMIT_S}s)"

# --- 10. Record ---------------------------------------------------------------------------
# Against the source in prod. The rehearsal's source is gone by design, so it records on
# the restored copy, which exercises the command and is then thrown away.
record_args=(drill record --mode "$MODE" --rpo-seconds "$RPO_OBSERVED" --rto-seconds "$RTO" --target "$T_MARKER")
if [ "$MODE" = prod ]; then
  docker exec "$SRC_APP" tumnis "${record_args[@]}" >&2 || fail "10 drill record: limits missed or not recorded"
else
  tumnis_on_scratch "${record_args[@]}" >&2 || fail "10 drill record: limits missed or not recorded"
fi
[ "$RTO" -le "$RTO_LIMIT_S" ] || fail "9 RTO missed: ${RTO}s > ${RTO_LIMIT_S}s"
echo "drill $MODE: rpo_seconds=$RPO_OBSERVED rto_seconds=$RTO target=$T_MARKER"
