#!/usr/bin/env bash
# =============================================================================
# git_hook_heartbeat_skip_ci.sh — Pre-commit hook: detects heartbeat-only
# audit_gaps.yaml diffs and appends [skip ci] to the commit message.
# Eliminates ~86% of CI workflow runs for trivial timestamp updates.
#
# Usage: copy to .git/hooks/prepare-commit-msg (recommended) or pre-commit.
# =============================================================================
set -euo pipefail

readonly AUDIT_FILE="audit_gaps.yaml"
readonly SKIP_CI_TAG="[skip ci]"
readonly -a HEARTBEAT_PATTERNS=(
    "last_heartbeat" "heartbeat_at" "heartbeat_timestamp"
    "last_check" "updated_at" "checked_at" "ping_at" "last_seen" "status_ts"
)

_cleanup() { local rc=$?; [[ -n "${_TMPDIR:-}" && -d "${_TMPDIR:-}" ]] && rm -rf "${_TMPDIR}"; exit "${rc}"; }
trap _cleanup EXIT INT TERM
_TMPDIR="$(mktemp -d)"

# get_staged_diff: Returns cached diff for audit_gaps.yaml (empty if none).
get_staged_diff() { git diff --cached -- "${AUDIT_FILE}" 2>/dev/null || true; }

# is_heartbeat_only_change: Returns 0 if all changed lines match heartbeat
# patterns (or diff is empty); returns 1 if non-heartbeat changes exist.
is_heartbeat_only_change() {
    local diff_content="$1"
    [[ -z "${diff_content}" ]] && return 0
    local changed_lines
    changed_lines="$(echo "${diff_content}" | grep -E '^[+-]' | grep -vE '^[+-]{3}' || true)"
    [[ -z "${changed_lines}" ]] && return 0
    local non_heartbeat=""
    while IFS= read -r line; do
        local matched=false
        for pattern in "${HEARTBEAT_PATTERNS[@]}"; do
            if echo "${line}" | grep -qi "${pattern}"; then matched=true; break; fi
        done
        [[ "${matched}" == "false" ]] && non_heartbeat="${non_heartbeat}${line}"$'\n'
    done <<< "${changed_lines}"
    local trimmed
    trimmed="$(echo "${non_heartbeat}" | sed '/^[[:space:]]*$/d')"
    [[ -z "${trimmed}" ]] && return 0
    return 1
}

# append_skip_ci: Appends [skip ci] tag to commit message file (idempotent).
append_skip_ci() {
    local commit_msg_file="$1"
    [[ ! -f "${commit_msg_file}" ]] && return 1
    grep -qF "${SKIP_CI_TAG}" "${commit_msg_file}" && return 0
    echo "" >> "${commit_msg_file}"
    echo "${SKIP_CI_TAG}" >> "${commit_msg_file}"
    return 0
}

# main: Entry point. $1=hook_type, $2=commit_msg_file (for prepare-commit-msg).
main() {
    local hook_type="${1:-pre-commit}" commit_msg_file="${2:-}"
    local staged_files
    staged_files="$(git diff --cached --name-only -- "${AUDIT_FILE}" 2>/dev/null || true)"
    [[ -z "${staged_files}" ]] && exit 0
    local diff_content
    diff_content="$(get_staged_diff)"
    if is_heartbeat_only_change "${diff_content}"; then
        if [[ "${hook_type}" == "prepare-commit-msg" && -n "${commit_msg_file}" ]]; then
            append_skip_ci "${commit_msg_file}"
            echo "[skip-ci-hook] Heartbeat-only change; [skip ci] appended." >&2
        else
            echo "[skip-ci-hook] Heartbeat-only change in ${AUDIT_FILE}." >&2
            echo "[skip-ci-hook] Install as prepare-commit-msg to auto-append [skip ci]." >&2
        fi
        exit 0
    else
        echo "[skip-ci-hook] Non-heartbeat changes in ${AUDIT_FILE}; CI runs normally." >&2
        exit 0
    fi
}

main "${@}"
