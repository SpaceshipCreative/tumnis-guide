#!/bin/sh
# CycloneDX SBOM of a built image (P0-16, SEC-7), for the release workflow (P0-30 owns
# release.yml and attaches the file to the release):
#
#   scripts/ci/sbom.sh ghcr.io/spaceshipcreative/tumnis:v1.2.3 sbom.cdx.json
#
# Same pinned Trivy as the Security job.
set -eu
IMAGE="${1:?usage: sbom.sh <image> <output.cdx.json>}"
OUT="${2:?usage: sbom.sh <image> <output.cdx.json>}"
TRIVY=aquasec/trivy:0.74.0@sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969
OUT_DIR="$(cd "$(dirname "$OUT")" && pwd)"
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$OUT_DIR:/out" "$TRIVY" \
  image --quiet --format cyclonedx --output "/out/$(basename "$OUT")" "$IMAGE"
