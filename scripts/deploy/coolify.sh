#!/usr/bin/env bash
# Coolify API calls for deploy.yml and preview.yml (homelab runner only: Coolify is on the
# private network, so GitHub cannot reach it and its auto-deploy stays off; plan B5 #15).
#
#   coolify.sh set-env  <KEY> <VALUE> [preview]   # application env var (preview: PR only)
#   coolify.sh deploy   [<pr-number>]             # deploy main, or the PR's preview; waits
#
# Needs COOLIFY_URL, COOLIFY_TOKEN (team API token, deploy + write) and COOLIFY_APP_UUID.
# Endpoints: https://coolify.io/docs/api-reference (verify against the installed version).
set -euo pipefail

: "${COOLIFY_URL:?}" "${COOLIFY_TOKEN:?}" "${COOLIFY_APP_UUID:?}"
API="${COOLIFY_URL%/}/api/v1"
TIMEOUT_S="${COOLIFY_TIMEOUT_S:-900}"

call() { # method path [json]
  curl -fsS -X "$1" "$API$2" -H "Authorization: Bearer $COOLIFY_TOKEN" \
    -H "Content-Type: application/json" ${3:+--data "$3"}
}

set_env() {
  local key=$1 value=$2 preview=${3:-}
  local body
  body=$(jq -nc --arg k "$key" --arg v "$value" --argjson p "$([ "$preview" = preview ] && echo true || echo false)" \
    '{key: $k, value: $v, is_preview: $p}')
  call PATCH "/applications/$COOLIFY_APP_UUID/envs" "$body" >/dev/null
}

deploy() {
  local pr=${1:-} query="uuid=$COOLIFY_APP_UUID&force=false"
  [ -n "$pr" ] && query="$query&pr=$pr"
  local deployment
  deployment=$(call GET "/deploy?$query" | jq -r '.deployments[0].deployment_uuid')
  [ -n "$deployment" ] && [ "$deployment" != null ] || { echo "no deployment started" >&2; exit 1; }
  echo "deployment $deployment"
  local waited=0 status
  while :; do
    status=$(call GET "/deployments/$deployment" | jq -r '.status')
    case "$status" in
      finished) echo "deployed"; return 0 ;;
      failed | cancelled*) echo "deployment $status" >&2; exit 1 ;;
    esac
    [ "$waited" -ge "$TIMEOUT_S" ] && { echo "deployment timed out ($status)" >&2; exit 1; }
    sleep 10; waited=$((waited + 10))
  done
}

case "${1:-}" in
  set-env) shift; set_env "$@" ;;
  deploy) shift; deploy "$@" ;;
  *) sed -n '5,7p' "$0" >&2; exit 2 ;;
esac
