#!/bin/sh
# rclone copy check (P0-28, REL-1): a file removed at the source stays at the destination.
#
#   scripts/drill/rclone_copy_check.sh REMOTE:PATH      # e.g. b2:tumnis-folders/drill/copy-check
#
# Writes a.txt in a temporary source, copies it with deploy/rclone/folders-backup.sh,
# deletes a.txt at the source, copies again, then requires `rclone lsf` to still list a.txt.
# The probe stays at the destination (the backup key cannot delete, by design); each run
# overwrites it. POSIX sh: it runs in the Alpine-based rclone image in the integration test.
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: $0 REMOTE:PATH" >&2
  exit 2
fi
remote=$1
here=$(cd "$(dirname "$0")" && pwd)
backup=${FOLDERS_BACKUP:-$here/../../deploy/rclone/folders-backup.sh}
probe=a.txt

src=$(mktemp -d)
trap 'rm -rf "$src"' EXIT INT TERM

echo "rclone copy check $(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$src/$probe"
sh "$backup" "$src" "$remote"
rm "$src/$probe"
sh "$backup" "$src" "$remote"

if rclone lsf "$remote" | grep -qx "$probe"; then
  echo "rclone_copy_check: ok ($probe kept at $remote after removal at the source)"
else
  echo "rclone_copy_check: FAILED ($probe missing at $remote)" >&2
  exit 1
fi
