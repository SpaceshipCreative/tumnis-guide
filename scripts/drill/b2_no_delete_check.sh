#!/usr/bin/env bash
# B2 no-delete check (P0-28, T-P0-28-03, REL-1, SEC-9): the pgBackRest backup key must not
# be able to destroy a file version in the backup bucket.
#
#   scripts/drill/b2_no_delete_check.sh
#
# With the backup key and the AWS CLI on the B2 S3 endpoint: put drill/probe-<ts>, read its
# VersionId, then `delete-object --version-id` must fail with AccessDenied (the key lacks
# deleteFiles). A delete without a version id succeeds as a hide marker, because hiding
# needs only writeFiles; a hidden file is recoverable, so the check then requires the
# version to still exist. Exits non-zero if the versioned delete succeeds or the version is
# gone. Probes stay in the bucket; the bucket's lifecycle rule removes them.
#
# The key comes from the same secrets file pgBackRest reads (repo2-s3-key,
# repo2-s3-key-secret), the bucket, endpoint and region from deploy/pgbackrest/pgbackrest.conf.
# Overrides: DRILL_PGBACKREST_CONF_D (default /etc/pgbackrest/conf.d), B2_ENDPOINT (URL),
# B2_BUCKET, B2_REGION, B2_DOCKER_ARGS (extra `docker run` arguments, e.g. a network).
# Host requirements: bash, docker. The AWS CLI runs from a pinned image.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
AWS_IMAGE="amazon/aws-cli:2.37.5@sha256:fb7ccfc7b4e3a05017e6c9ded4ba959b99af622130e211fb976c68c2f4d3f224"
SECRETS="${DRILL_PGBACKREST_CONF_D:-/etc/pgbackrest/conf.d}/secrets.conf"
CONF="$REPO_ROOT/deploy/pgbackrest/pgbackrest.conf"

fail() {
  echo "b2_no_delete_check: FAILED: $*" >&2
  exit 1
}
# The last `key=value` for an option in a pgBackRest ini file (comments sit on own lines).
conf_value() { sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$2" | tail -1; }

[ -r "$SECRETS" ] || fail "cannot read $SECRETS"
AWS_ACCESS_KEY_ID="$(conf_value repo2-s3-key "$SECRETS")"
AWS_SECRET_ACCESS_KEY="$(conf_value repo2-s3-key-secret "$SECRETS")"
AWS_DEFAULT_REGION="${B2_REGION:-$(conf_value repo2-s3-region "$CONF")}"
ENDPOINT="${B2_ENDPOINT:-https://$(conf_value repo2-s3-endpoint "$CONF")}"
BUCKET="${B2_BUCKET:-$(conf_value repo2-s3-bucket "$CONF")}"
[ -n "$AWS_ACCESS_KEY_ID" ] && [ -n "$AWS_SECRET_ACCESS_KEY" ] || fail "no repo2 key in $SECRETS"
export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_DEFAULT_REGION

read -r -a EXTRA <<<"${B2_DOCKER_ARGS:-}"
# Credentials reach the container from this environment (-e NAME), never on a command line.
aws() {
  docker run --rm -i ${EXTRA[@]+"${EXTRA[@]}"} -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY \
    -e AWS_DEFAULT_REGION -e AWS_CA_BUNDLE "$AWS_IMAGE" --endpoint-url "$ENDPOINT" "$@"
}
version_of() {
  aws s3api list-object-versions --bucket "$BUCKET" --prefix "$KEY" \
    --query "Versions[?Key=='$KEY' && VersionId=='$1'] | [0].VersionId" --output text
}

KEY="drill/probe-$(date -u +%Y%m%dT%H%M%SZ)"
echo "no-delete probe $(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  | aws s3 cp --only-show-errors - "s3://$BUCKET/$KEY" \
  || fail "upload with the backup key failed"
VERSION="$(aws s3api list-object-versions --bucket "$BUCKET" --prefix "$KEY" \
  --query "Versions[?Key=='$KEY'] | [0].VersionId" --output text)"
[ -n "$VERSION" ] && [ "$VERSION" != None ] || fail "no VersionId for $KEY"
echo "b2_no_delete_check: put $BUCKET/$KEY version $VERSION"

if output="$(aws s3api delete-object --bucket "$BUCKET" --key "$KEY" --version-id "$VERSION" 2>&1)"; then
  fail "the backup key deleted version $VERSION of $KEY: it has deleteFiles"
fi
grep -q AccessDenied <<<"$output" || fail "versioned delete failed, but not with AccessDenied: $output"
echo "b2_no_delete_check: versioned delete denied (AccessDenied)"

# Hiding is allowed (writeFiles) and must leave the version in place.
if aws s3api delete-object --bucket "$BUCKET" --key "$KEY" >/dev/null 2>&1; then
  echo "b2_no_delete_check: plain delete made a hide marker"
fi
[ "$(version_of "$VERSION")" = "$VERSION" ] || fail "version $VERSION of $KEY is gone"
echo "b2_no_delete_check: ok (version $VERSION still exists)"
