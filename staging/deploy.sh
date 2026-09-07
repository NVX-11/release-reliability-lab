#!/usr/bin/env bash
set -Eeuo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
controller=(python staging/release_controller.py)
compose=(docker compose --project-name release-reliability-staging --file staging/compose.yml)
generated=staging/generated
mkdir -p "$generated"

started_at=$SECONDS
promotion="not attempted"
active_ref=""
candidate_ref=""
previous_ref=""
metadata_active='{}'
metadata_candidate='{}'
active_validation="not run"
candidate_validation="not run"
active_ready_seconds="n/a"
candidate_ready_seconds="n/a"
promotion_seconds="n/a"

summary() {
  local result=$1
  local new_active="not promoted"
  [[ "$promotion" == successful* ]] && new_active="$candidate_ref"
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    {
      echo "## Ephemeral staging deployment"
      echo "| Field | Value |"
      echo "| --- | --- |"
      echo "| Controller commit | \`${GITHUB_SHA:-local}\` |"
      echo "| Requested active digest | \`${active_ref:-validation failed}\` |"
      echo "| Requested candidate digest | \`${candidate_ref:-validation failed}\` |"
      echo "| Active image metadata | \`$metadata_active\` |"
      echo "| Candidate image metadata | \`$metadata_candidate\` |"
      echo "| Active validation | $active_validation |"
      echo "| Candidate validation | $candidate_validation |"
      echo "| Previous immutable release | \`${previous_ref:-not started}\` |"
      echo "| Newly active immutable release | \`$new_active\` |"
      echo "| Promotion | $promotion |"
      echo "| Readiness timings | active: $active_ready_seconds s; candidate: $candidate_ready_seconds s |"
      echo "| Promotion timing | $promotion_seconds seconds |"
      echo "| Result | $([[ $result == 0 ]] && echo success || echo failure) |"
      echo "| Elapsed | $((SECONDS - started_at)) seconds |"
    } >> "$GITHUB_STEP_SUMMARY"
  fi
  return "$result"
}
cleanup() {
  local result=$?
  summary "$result" || true
  if (( result != 0 )); then
    echo "Staging deployment failed; container state and logs follow:" >&2
    "${compose[@]}" ps --all >&2 || true
    "${compose[@]}" logs --no-color >&2 || true
  fi
  "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  docker logout ghcr.io >/dev/null 2>&1 || true
  rm -rf "$generated"
  exit "$result"
}
trap cleanup EXIT

active_ref=$("${controller[@]}" normalize "${BASELINE_IMAGE:?BASELINE_IMAGE is required}")
candidate_ref=$("${controller[@]}" normalize "${CANDIDATE_IMAGE_INPUT:?CANDIDATE_IMAGE_INPUT is required}")
export ACTIVE_IMAGE="$active_ref" CANDIDATE_IMAGE="$candidate_ref"

printf '%s' "${GHCR_TOKEN:?GHCR_TOKEN is required}" | docker login ghcr.io --username "${GHCR_USER:?GHCR_USER is required}" --password-stdin
docker pull "$active_ref"
docker pull "$candidate_ref"
docker image inspect "$active_ref" > "$generated/active-inspect.json"
docker image inspect "$candidate_ref" > "$generated/candidate-inspect.json"
active_validation="failed or interrupted during metadata validation"
metadata_active=$("${controller[@]}" metadata "$active_ref" "$generated/active-inspect.json")
active_validation="metadata passed; endpoint validation not completed"
candidate_validation="failed or interrupted during metadata validation"
metadata_candidate=$("${controller[@]}" metadata "$candidate_ref" "$generated/candidate-inspect.json")
candidate_validation="metadata passed; endpoint validation not completed"
active_version=$(python -c 'import json,sys; print(json.loads(sys.argv[1])["version"])' "$metadata_active")
candidate_version=$(python -c 'import json,sys; print(json.loads(sys.argv[1])["version"])' "$metadata_candidate")

"${controller[@]}" render active "$generated/nginx.conf"
"${compose[@]}" up --detach --no-build active proxy
readiness_started=$SECONDS
for _ in {1..30}; do
  "${controller[@]}" http http://127.0.0.1:8080 /health && break
  sleep 1
done
"${controller[@]}" http http://127.0.0.1:8080 /health
"${controller[@]}" http http://127.0.0.1:8080 /version --version "$active_version"
active_ready_seconds=$((SECONDS - readiness_started))
active_validation="passed metadata, readiness, health, and version"
previous_ref="$active_ref"

"${compose[@]}" up --detach --no-build candidate
readiness_started=$SECONDS
candidate_ok=false
for _ in {1..30}; do
  if body=$("${compose[@]}" exec -T proxy wget -qO- http://candidate:8000/health 2>/dev/null) && \
    [[ "$body" == '{"status":"healthy"}' ]]; then candidate_ok=true; break; fi
  sleep 1
done
[[ "$candidate_ok" == true ]] || {
  candidate_validation="failed readiness/health; active route unchanged"
  promotion="rejected: candidate readiness failed; active unchanged"; exit 1;
}
[[ "$("${compose[@]}" exec -T proxy wget -qO- http://candidate:8000/version)" == "{\"version\":\"$candidate_version\"}" ]] || {
  candidate_validation="failed version; active route unchanged"
  promotion="rejected: candidate version failed; active unchanged"; exit 1;
}
candidate_ready_seconds=$((SECONDS - readiness_started))
candidate_validation="passed metadata, readiness, health, and version"

# Test the proposed complete configuration before atomically replacing the live file.
promotion_started=$SECONDS
"${controller[@]}" render candidate "$generated/nginx.next.conf"
"${compose[@]}" exec -T proxy nginx -t -c /etc/nginx/generated/nginx.next.conf
mv "$generated/nginx.next.conf" "$generated/nginx.conf"
if ! "${compose[@]}" exec -T proxy nginx -s reload -c /etc/nginx/generated/nginx.conf; then
  "${controller[@]}" render active "$generated/nginx.conf"
  "${compose[@]}" exec -T proxy nginx -s reload -c /etc/nginx/generated/nginx.conf || true
  promotion="failed while applying route; previous route restored"
  exit 1
fi
if ! "${controller[@]}" http http://127.0.0.1:8080 /health || \
   ! "${controller[@]}" http http://127.0.0.1:8080 /version --version "$candidate_version"; then
  "${controller[@]}" render active "$generated/nginx.conf"
  "${compose[@]}" exec -T proxy nginx -t -c /etc/nginx/generated/nginx.conf
  "${compose[@]}" exec -T proxy nginx -s reload -c /etc/nginx/generated/nginx.conf
  promotion="failed post-promotion validation; previous route restored"
  exit 1
fi
"${compose[@]}" exec -T proxy wget -qO- http://active:8000/health | python -c 'import json,sys; assert json.load(sys.stdin) == {"status":"healthy"}'
promotion_seconds=$((SECONDS - promotion_started))
promotion="successful; previous release remains running"
