#!/usr/bin/env bash
# Restore drill (A0.4, REL-1): point-in-time restore of the B2 repository (repo 2) to a
# scratch container, with RPO under 15 minutes and RTO under 1 hour.
#
#   scripts/drill/restore_drill.sh --mode prod|rehearsal
#
# Stub committed by P0-05 with the red acceptance suite: it exits 1 until P0-28 fills in
# the ten steps (preflight, marker, fence, wait for archive, loss, restore, scratch
# server, checks, stop the clock, `tumnis drill record`).
set -euo pipefail

mode=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --mode) mode="${2:-}"; shift 2 ;;
    *) echo "restore_drill: unknown argument: $1" >&2; exit 2 ;;
  esac
done
case "$mode" in
  prod | rehearsal) ;;
  *) echo "restore_drill: --mode must be prod or rehearsal" >&2; exit 2 ;;
esac

echo "restore_drill: not implemented yet (P0-28); the ${mode} drill fails until it lands" >&2
exit 1
