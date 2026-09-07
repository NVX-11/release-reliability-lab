#!/usr/bin/env bash
set -Eeuo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"

# Compose interpolates the complete model even for `down`. These inert defaults
# make cleanup independent of deploy.sh's process-local exports. No container is
# created and no image is pulled by `down`.
readonly cleanup_image="ghcr.io/nvx-11/release-reliability-lab@sha256:1c3462d30f0d9e1c3fadfd2163ecae4828853e7c34f8a5bbf6cd9694acfda938"
export ACTIVE_IMAGE="${ACTIVE_IMAGE:-$cleanup_image}"
export CANDIDATE_IMAGE="${CANDIDATE_IMAGE:-$cleanup_image}"

cleanup_result=0
docker compose \
  --project-name release-reliability-staging \
  --file staging/compose.yml \
  down --volumes --remove-orphans || cleanup_result=$?
docker logout ghcr.io >/dev/null 2>&1 || true
rm -rf staging/generated
if (( cleanup_result != 0 )); then
  echo "Staging Compose cleanup failed with exit code $cleanup_result" >&2
fi
exit "$cleanup_result"
