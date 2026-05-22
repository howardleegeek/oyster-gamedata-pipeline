#!/usr/bin/env bash
# sync_gcp_backend_release.sh — sync GCP backend appcast metadata to GitHub latest release.
#
# Usage:
#   bash scripts/sync_gcp_backend_release.sh
#
# Environment:
#   GCP_BACKEND_HOST          SSH host for the GCP backend (default: gamedata-backend)
#   BACKEND_URL               Public backend URL (default: http://136.109.41.170:8081)
#   SYSTEMD_SERVICE           systemd service name (default: oyster-backend-stub.service)
#   SYSTEMD_UNIT              systemd unit path (default: /etc/systemd/system/oyster-backend-stub.service)
#   RELEASE_ENV_FILE          remote env file to write release metadata
#   GITHUB_REPOSITORY         optional owner/repo override for gh release commands
#   SKIP_SMOKE=true           skip deployed backend smoke after restart
#   RUN_E2E=true              also run recorder/backend E2E after smoke

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

GCP_BACKEND_HOST="${GCP_BACKEND_HOST:-gamedata-backend}"
BACKEND_URL="${BACKEND_URL:-http://136.109.41.170:8081}"
SYSTEMD_SERVICE="${SYSTEMD_SERVICE:-oyster-backend-stub.service}"
SYSTEMD_UNIT="${SYSTEMD_UNIT:-/etc/systemd/system/oyster-backend-stub.service}"
RELEASE_ENV_FILE="${RELEASE_ENV_FILE:-/home/howardli/oyster-backend-stub/state/recorder-release.env}"

log() {
  echo "[gcp-release-sync] $*"
}

die() {
  echo "[gcp-release-sync] ERROR: $*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "$1 is required"
}

gh_release() {
  if [ -n "${GITHUB_REPOSITORY:-}" ]; then
    gh release "$@" -R "$GITHUB_REPOSITORY"
  else
    gh release "$@"
  fi
}

shell_single_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

require_command gh
require_command ssh
require_command python3
require_command curl

tag="${RECORDER_RELEASE_TAG:-}"
if [ -z "$tag" ]; then
  tag=$(gh_release list --limit 1 --json tagName --jq '.[0].tagName')
fi

if ! [[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  die "Invalid recorder release tag: ${tag}"
fi

version="${tag#v}"
asset_row=$(
  gh_release view "$tag" \
    --json assets \
    --jq '.assets[]
      | select(.name | test("^OysterRecorder-[Ss]etup-.*\\.exe$"))
      | [.name, .url, .digest]
      | @tsv' \
    | head -n 1
)

if [ -z "$asset_row" ]; then
  die "No OysterRecorder installer asset found on ${tag}"
fi

IFS=$'\t' read -r installer_name installer_url installer_digest <<< "$asset_row"
if [[ "$installer_digest" != sha256:* ]]; then
  die "Installer ${installer_name} is missing a sha256 digest"
fi
sha256="${installer_digest#sha256:}"

log "Resolved ${tag}: ${installer_name}"
log "Backend host: ${GCP_BACKEND_HOST}"
log "Backend URL: ${BACKEND_URL}"

env_content=$(
  cat <<EOF
OYSTER_RECORDER_RELEASE_VERSION=${version}
OYSTER_RECORDER_RELEASE_TAG=${tag}
OYSTER_RECORDER_DOWNLOAD_URL=${installer_url}
OYSTER_RECORDER_SHA256=${sha256}
EOF
)

remote_env_dir="$(dirname "$RELEASE_ENV_FILE")"
quoted_env_file="$(shell_single_quote "$RELEASE_ENV_FILE")"
quoted_env_dir="$(shell_single_quote "$remote_env_dir")"
quoted_unit="$(shell_single_quote "$SYSTEMD_UNIT")"
quoted_service="$(shell_single_quote "$SYSTEMD_SERVICE")"

log "Writing release metadata to ${GCP_BACKEND_HOST}:${RELEASE_ENV_FILE}"
# shellcheck disable=SC2029 # quoted paths are intentionally expanded locally before SSH.
ssh "$GCP_BACKEND_HOST" "
  set -euo pipefail
  mkdir -p ${quoted_env_dir}
  chmod 700 ${quoted_env_dir}
  umask 077
  cat > ${quoted_env_file}
  chmod 600 ${quoted_env_file}
" <<< "$env_content"

log "Installing EnvironmentFile and restarting ${SYSTEMD_SERVICE}"
# shellcheck disable=SC2029 # quoted paths are intentionally expanded locally before SSH.
ssh "$GCP_BACKEND_HOST" "
  set -euo pipefail
  sudo sed -i '/^Environment=OYSTER_RECORDER_RELEASE_/d' ${quoted_unit}
  if ! grep -Fxq 'EnvironmentFile=${RELEASE_ENV_FILE}' ${quoted_unit}; then
    if grep -q '^EnvironmentFile=' ${quoted_unit}; then
      sudo sed -i '/^EnvironmentFile=/a EnvironmentFile=${RELEASE_ENV_FILE}' ${quoted_unit}
    else
      sudo sed -i '/^Environment=PYTHONPATH=/a EnvironmentFile=${RELEASE_ENV_FILE}' ${quoted_unit}
    fi
  fi
  sudo systemctl daemon-reload
  sudo systemctl restart ${quoted_service}
  systemctl is-active ${quoted_service}
"

log "Waiting for ${BACKEND_URL}/healthz"
ready=false
for _ in {1..20}; do
  if curl -fsS "${BACKEND_URL%/}/healthz" >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done

if [ "$ready" != "true" ]; then
  die "Backend did not become ready at ${BACKEND_URL}/healthz"
fi

if [ "${SKIP_SMOKE:-false}" != "true" ]; then
  log "Running deployed backend smoke against ${BACKEND_URL}"
  python3 "$PROJECT_ROOT/scripts/verify_deployed_backend.py" \
    --url "$BACKEND_URL" \
    --verbose \
    --expected-recorder-tag "$tag"
fi

if [ "${RUN_E2E:-false}" = "true" ]; then
  log "Running recorder/backend E2E against ${BACKEND_URL}"
  python3 "$PROJECT_ROOT/bin/remote_recorder_backend_e2e.py" \
    --backend-url "$BACKEND_URL"
fi

log "PASS ${GCP_BACKEND_HOST} appcast synced to ${tag}"
