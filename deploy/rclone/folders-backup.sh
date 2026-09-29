#!/bin/sh
# Folder backup (P0-28, REL-1): copy one project folder to a backup remote. P1-15 schedules
# it nightly for every Tumnis-made folder.
#
#   folders-backup.sh SRC REMOTE:PATH
#
# Copy, never sync: a file deleted or emptied at the source stays in the backup, so a
# mistake (or ransomware) at the source cannot reach the copy. The remote comes from the
# rclone config (RCLONE_CONFIG_<NAME>_* variables or rclone.conf); on B2 use a key without
# deleteFiles. --checksum compares hashes, not sizes and times. POSIX sh on purpose: it
# also runs in the Alpine-based rclone image.
set -eu

if [ "$#" -ne 2 ]; then
  echo "usage: $0 SRC REMOTE:PATH" >&2
  exit 2
fi

exec rclone copy --checksum --create-empty-src-dirs "$1" "$2"
