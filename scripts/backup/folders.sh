#!/bin/sh
# Nightly folder backup (P1-15, REL-1): copy every project folder the manifest lists to its
# own place under the backup remote.
#
#   folders.sh REMOTE:PATH
#
# The manifest is `tumnis knowledge backup-sources --json`: every Tumnis-made folder and
# every existing folder whose project opted in, as [{"source", "dest", ...}]. Each source
# goes to REMOTE:PATH/<dest> through deploy/rclone/folders-backup.sh, which only copies
# (a file removed at the source stays in the backup). One failed folder does not stop the
# others; the job exits non-zero when any failed. POSIX sh on purpose (it also runs in the
# Alpine-based rclone image, with python3 for the JSON).
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: $0 REMOTE:PATH" >&2
  exit 2
fi

remote=${1%/}
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
copy_one="$here/../../deploy/rclone/folders-backup.sh"
tab=$(printf '\t')

manifest=$(tumnis knowledge backup-sources --json)
pairs=$(printf '%s' "$manifest" | python3 -c '
import json, sys
for item in json.load(sys.stdin):
    source, dest = item["source"], item["dest"]
    if any(c in source + dest for c in "\t\n"):
        sys.exit("folders.sh: a tab or newline in a backup path")
    print(source + "\t" + dest)
')

failed=0
while IFS="$tab" read -r source dest; do
  [ -n "$source" ] || continue
  if ! /bin/sh "$copy_one" "$source" "$remote/$dest"; then
    echo "folders.sh: copy of $source failed" >&2
    failed=1
  fi
done <<EOF
$pairs
EOF

exit "$failed"
