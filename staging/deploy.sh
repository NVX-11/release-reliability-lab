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
negative_path="not run"
recovery="not required"

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
      echo "| Negative-path check | $negative_path |"
      echo "| Promotion | $promotion |"
      echo "| Recovery | $recovery |"
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
active_revision=$(python -c 'import json,sys; print(json.loads(sys.argv[1])["revision"])' "$metadata_active")
candidate_revision=$(python -c 'import json,sys; print(json.loads(sys.argv[1])["revision"])' "$metadata_candidate")

verify_stable() {
  local version=$1 digest=$2 revision=$3
  for _ in {1..15}; do
    if "${controller[@]}" http http://127.0.0.1:8080 /health \
         --digest "$digest" --revision "$revision" && \
       "${controller[@]}" http http://127.0.0.1:8080 /version --version "$version" \
         --digest "$digest" --revision "$revision"; then
      return 0
    fi
    sleep 1
  done
  echo "Stable route did not serve the expected validated release within 15 attempts" >&2
  return 1
}

restore_active() {
  recovery="attempting restoration of previous validated route"
  "${controller[@]}" render active "$active_ref" "$active_revision" "$generated/nginx.restore.conf" || {
    recovery="unresolved: failed to render previous route"; return 1;
  }
  "${compose[@]}" exec -T proxy nginx -t -c /etc/nginx/generated/nginx.restore.conf || {
    recovery="unresolved: restored configuration failed validation"; return 1;
  }
  mv "$generated/nginx.restore.conf" "$generated/nginx.conf"
  "${compose[@]}" exec -T proxy nginx -s reload -c /etc/nginx/generated/nginx.conf || {
    recovery="unresolved: failed to apply previous route"; return 1;
  }
  verify_stable "$active_version" "$active_ref" "$active_revision" || {
    recovery="unresolved: previous route failed health, version, or identity verification"; return 1;
  }
  recovery="successful: previous validated route restored and verified"
}

"${controller[@]}" render active "$active_ref" "$active_revision" "$generated/nginx.conf"
"${compose[@]}" up --detach --no-build active proxy
readiness_started=$SECONDS
verify_stable "$active_version" "$active_ref" "$active_revision"
active_ready_seconds=$((SECONDS - readiness_started))
active_validation="passed metadata, readiness, health, version, and release identity"
previous_ref="$active_ref"

"${compose[@]}" up --detach --no-build candidate
readiness_started=$SECONDS
candidate_ok=false
for _ in {1..30}; do
  if body=$("${compose[@]}" exec -T proxy wget -qO- -T 2 http://candidate:8000/health 2>/dev/null) && \
    [[ "$body" == '{"status":"healthy"}' ]]; then candidate_ok=true; break; fi
  sleep 1
done
[[ "$candidate_ok" == true ]] || {
  candidate_validation="failed readiness/health; active route unchanged"
  promotion="rejected: candidate readiness failed; active unchanged"
  verify_stable "$active_version" "$active_ref" "$active_revision" || true
  exit 1
}
candidate_body=$("${compose[@]}" exec -T proxy wget -qO- -T 2 http://candidate:8000/version)
validate_candidate_version() {
  [[ "$candidate_body" == "{\"version\":\"$1\"}" ]]
}
validate_candidate_version "$candidate_version" || {
  candidate_validation="failed version; active route unchanged"
  promotion="rejected: candidate version failed; active unchanged"
  verify_stable "$active_version" "$active_ref" "$active_revision" || true
  exit 1
}
candidate_ready_seconds=$((SECONDS - readiness_started))
candidate_validation="passed metadata, readiness, health, and version"

# Controlled rejection evidence: a deliberately impossible expected version must
# fail candidate validation, while the validated active route remains unchanged.
if validate_candidate_version "__must-not-match__"; then
  negative_path="failed: invalid candidate expectation unexpectedly passed"
  exit 1
fi
verify_stable "$active_version" "$active_ref" "$active_revision"
negative_path="passed: failed candidate expectation left active identity unchanged"

promotion_started=$SECONDS
promotion="attempting candidate route"
if ! "${controller[@]}" render candidate "$candidate_ref" "$candidate_revision" "$generated/nginx.next.conf" || \
   ! "${compose[@]}" exec -T proxy nginx -t -c /etc/nginx/generated/nginx.next.conf; then
  promotion="failed before route reload"
  restore_active || true
  exit 1
fi
mv "$generated/nginx.next.conf" "$generated/nginx.conf"
if ! "${compose[@]}" exec -T proxy nginx -s reload -c /etc/nginx/generated/nginx.conf; then
  promotion="failed while applying candidate route"
  restore_active || true
  exit 1
fi
if ! verify_stable "$candidate_version" "$candidate_ref" "$candidate_revision"; then
  promotion="failed post-reload health, version, or identity verification"
  restore_active || true
  exit 1
fi
"${compose[@]}" exec -T proxy wget -qO- -T 2 http://active:8000/health | \
  python -c 'import json,sys; assert json.load(sys.stdin) == {"status":"healthy"}'
promotion_seconds=$((SECONDS - promotion_started))
promotion="successful: candidate identity verified through stable route"
