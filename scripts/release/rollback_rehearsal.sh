#!/usr/bin/env bash
# Rollback rehearsal (P0-30, REL-4): deploy N, then N+1, then N again on one database, and
# fail unless every step stays healthy. Proves N+1's migrations are expand-only: N serves
# N+1's schema, and N's `tumnis migrate` exits 0 saying the database is ahead.
#
#   scripts/release/rollback_rehearsal.sh N N1
#   scripts/release/rollback_rehearsal.sh v0.0.9 HEAD
#   scripts/release/rollback_rehearsal.sh ghcr.io/spaceshipcreative/tumnis:main "$GITHUB_SHA"
#
# N and N1 each name a git ref or an image:
#   - a release tag (v1.2.3): the registry image of that tag, built from the tag if it
#     cannot be pulled;
#   - any other git ref (a SHA, HEAD, a branch): built from `git archive <ref>`;
#   - anything else is an image reference, pulled (or used as it is when only local).
#
# The stack is deploy/compose.test.yaml as compose project tumnis-skew (fakes, the seed
# set, api on 127.0.0.1:$TUMNIS_SKEW_PORT); TUMNIS_IMAGE swaps the image between steps.
# Environment: TUMNIS_SKEW_PORT (default 18080), TUMNIS_REGISTRY_IMAGE (default
# ghcr.io/spaceshipcreative/tumnis), TUMNIS_KEY (bearer for the data smoke once the
# projects and tasks routes exist), KEEP_STACK=1 (leave the stack up to debug).
#
# Steps (plan P0-30):
#   1. Postgres and PgBouncer; migrate from N; seed; api and worker from N; smoke N.
#   2. migrate from N+1 (expand only); api and worker to N+1; smoke N+1; write rows.
#   3. api and worker back to N; N's migrate exits 0 ("database is ahead" when N+1 added
#      revisions); smoke N, reading rows N+1 wrote and writing new ones.
#   4. Fail on any 5xx in the api logs across the run, or a non-200 readiness at any smoke.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PROJECT=tumnis-skew
PORT="${TUMNIS_SKEW_PORT:-18080}"
BASE="http://127.0.0.1:${PORT}"
REGISTRY_IMAGE="${TUMNIS_REGISTRY_IMAGE:-ghcr.io/spaceshipcreative/tumnis}"
WORK="$(mktemp -d)"
LOGS="${WORK}/api.log"
IMAGE=""

log() { echo "rollback_rehearsal: $*" >&2; }
fail() {
  log "FAIL: $*"
  exit 1
}

[ $# -eq 2 ] || fail "usage: rollback_rehearsal.sh <N: tag, ref or image> <N+1: tag, ref or image>"
cd "$REPO"

# compose.test keeps WAL archiving off (issue #21), so the rehearsal needs no pgBackRest
# stanza and starts no backup service.
compose() {
  TUMNIS_IMAGE="$IMAGE" TUMNIS_TEST_PORT="$PORT" \
    docker compose -p "$PROJECT" -f deploy/compose.test.yaml "$@"
}

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    compose logs --no-color --tail=100 api worker migrate >&2 2>/dev/null || true
  fi
  if [ "${KEEP_STACK:-0}" = 1 ]; then
    log "KEEP_STACK=1: stack $PROJECT left up (docker compose -p $PROJECT -f deploy/compose.test.yaml down -v)"
  else
    compose down -v --remove-orphans >/dev/null 2>&1 || true
  fi
  rm -rf "$WORK"
  exit "$status"
}
trap cleanup EXIT

# --- images -------------------------------------------------------------------------------

build_ref() { # <git ref> <VERSION>: the image built from that commit's tree
  local sha tag dir
  sha="$(git rev-parse --verify "$1^{commit}")"
  tag="tumnis:skew-${sha:0:12}"
  if ! docker image inspect "$tag" >/dev/null 2>&1; then
    log "building $1 ($sha) as $tag"
    dir="$(mktemp -d)"
    git archive "$sha" | tar -x -C "$dir"
    docker build -q -f "$dir/deploy/Dockerfile" --build-arg "VERSION=$2" -t "$tag" "$dir" >&2
    rm -rf "$dir"
  fi
  echo "$tag"
}

resolve() { # <tag, git ref or image> -> a local image
  local ref="$1" sha
  if sha="$(git rev-parse -q --verify "${ref}^{commit}" 2>/dev/null)"; then
    if git rev-parse -q --verify "refs/tags/${ref}" >/dev/null; then
      if docker pull -q "${REGISTRY_IMAGE}:${ref}" >&2 2>/dev/null; then
        echo "${REGISTRY_IMAGE}:${ref}"
        return
      fi
      log "no ${REGISTRY_IMAGE}:${ref} in the registry; building the tag"
      build_ref "$ref" "$ref"
      return
    fi
    build_ref "$ref" "sha-${sha}"
    return
  fi
  docker pull -q "$ref" >&2 || docker image inspect "$ref" >/dev/null 2>&1 \
    || fail "$ref is neither a git ref nor an image this host can pull"
  echo "$ref"
}

# --- steps --------------------------------------------------------------------------------

use() { # <image>: the image the next compose command runs
  IMAGE="$1"
}

keep_logs() { # the api's logs, before its container is replaced
  compose logs --no-color api >>"$LOGS" 2>/dev/null || true
}

migrate() { # prints migrate's output; fails the run when migrate fails
  local out
  if ! out="$(compose run --rm --no-deps --pull never migrate 2>&1)"; then
    echo "$out" >&2
    fail "migrate from $IMAGE failed"
  fi
  echo "$out"
}

roll() { # api and worker onto the current image, waiting for the api's readiness check
  keep_logs
  compose up -d --no-deps --pull never --wait --wait-timeout 180 api worker \
    || fail "api or worker from $IMAGE never became healthy"
}

revisions() {
  compose exec -T -u postgres postgres psql -d tumnis -Atc \
    "SELECT string_agg(version_num, ',' ORDER BY version_num) FROM alembic_version"
}

# The data smoke (plan: create a project and a task, move it, list), once the projects and
# tasks routes exist (P0-17, P0-18). $1 = step label, $2 = a title the list must show.
data_smoke() {
  local label="$1" expect="${2:-}"
  if ! curl -fsS "$BASE/v1/openapi.json" | grep -q '"/v1/tasks"'; then
    log "$label: no /v1/tasks route in this release yet; data smoke skipped"
    echo ""
    return
  fi
  if [ -z "${TUMNIS_KEY:-}" ]; then
    log "::warning::$label: TUMNIS_KEY unset; data smoke skipped"
    echo ""
    return
  fi
  BASE="$BASE" LABEL="$label" EXPECT="$expect" python3 - <<'PY'
import json, os, sys, urllib.request, uuid

base, label, expect = os.environ["BASE"], os.environ["LABEL"], os.environ["EXPECT"]
headers = {"Authorization": f"Bearer {os.environ['TUMNIS_KEY']}", "Content-Type": "application/json"}


def call(method, path, body=None):
    request = urllib.request.Request(
        base + path, method=method, data=None if body is None else json.dumps(body).encode(),
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        return json.load(response)


title = f"skew {label} {uuid.uuid4().hex[:8]}"
project = call("POST", "/v1/projects", {"name": f"skew {label}"})
task = call("POST", "/v1/tasks", {"project_id": project["id"], "title": title})
call("POST", f"/v1/tasks/{task['id']}/status", {"to": "today", "version": task["version"]})
listed = json.dumps(call("GET", "/v1/tasks?limit=200"))
missing = [t for t in (title, expect) if t and t not in listed]
if missing:
    sys.exit(f"{label}: tasks list lacks {missing}")
print(title)
PY
}

smoke() { # <label> [a task title the list must show]: echoes the title this smoke wrote
  local label="$1" code version
  code="$(curl -s -o "$WORK/ready.json" -w '%{http_code}' "$BASE/health/ready")" || true
  [ "$code" = 200 ] || fail "$label: /health/ready answered $code: $(cat "$WORK/ready.json" 2>/dev/null)"
  version="$(curl -fsS -D - -o /dev/null "$BASE/health/live" | tr -d '\r' \
    | awk -F': ' 'tolower($1) == "tumnis-version" { print $2 }')"
  log "$label: ready ($(cat "$WORK/ready.json")); version ${version:-not reported}"
  data_smoke "$label" "${2:-}" || fail "$label: data smoke failed"
}

N_IMAGE="$(resolve "$1")"
N1_IMAGE="$(resolve "$2")"
log "N = $N_IMAGE, N+1 = $N1_IMAGE"
compose down -v --remove-orphans >/dev/null 2>&1 || true

log "1. deploy N"
use "$N_IMAGE"
compose up -d --wait --wait-timeout 180 postgres pgbouncer >&2
migrate >&2
compose run --rm --no-deps --pull never seed >&2 || fail "seed from N failed"
roll
smoke "N" >/dev/null
before="$(revisions)" || fail "cannot read alembic_version"

log "2. deploy N+1 (expand migrations only)"
use "$N1_IMAGE"
migrate >&2
after="$(revisions)" || fail "cannot read alembic_version"
roll
n1_title="$(smoke "N+1")"

log "3. roll back to N"
use "$N_IMAGE"
roll
out="$(migrate)"
echo "$out" >&2
if [ "$before" != "$after" ]; then
  grep -q "database is ahead" <<<"$out" \
    || fail "N+1 added revisions ($before -> $after) but N's migrate did not report the database ahead"
  [ "$(revisions)" = "$after" ] || fail "N's migrate changed alembic_version"
else
  log "N+1 added no revisions; the rollback runs on the same schema"
fi
smoke "N again" "$n1_title" >/dev/null

log "4. api logs"
keep_logs
if grep -En 'HTTP/[0-9.]+\\?" 5[0-9]{2}' "$LOGS" >&2; then
  fail "the api answered 5xx during the rehearsal"
fi
log "ok: N -> N+1 -> N stayed healthy"
