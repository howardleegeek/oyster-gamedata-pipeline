#!/usr/bin/env bash
#
# auto_merge_green_prs.sh — Scan open PRs and auto squash-merge those that are green.
#
# Usage:
#   bash scripts/auto_merge_green_prs.sh [--dry-run] [--auto] [--max N]
#
# Options:
#   --dry-run   List PRs that would be merged without actually merging them.
#   --auto      Skip label requirement; merge PRs from feat/SXX-cluster* branches.
#   --max N     Maximum number of PRs to merge in one run (default: unlimited).
#
set -euo pipefail

# ── Defaults ────────────────────────────────────────────────────────────────
DRY_RUN=false
AUTO_MODE=false
MAX_MERGES=0   # 0 = unlimited
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FAILURE_LOG="$PROJECT_ROOT/dashboard/merge_failures.log"

# ── Argument parsing ────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --auto)
      AUTO_MODE=true
      shift
      ;;
    --max)
      if [[ -z "${2:-}" || ! "$2" =~ ^[0-9]+$ ]]; then
        echo "ERROR: --max requires a positive integer argument" >&2
        exit 1
      fi
      MAX_MERGES="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [--dry-run] [--auto] [--max N]"
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

# ── Helpers ─────────────────────────────────────────────────────────────────

log_info() {
  echo "[INFO]  $(date '+%Y-%m-%d %H:%M:%S') $*"
}

log_warn() {
  echo "[WARN]  $(date '+%Y-%m-%d %H:%M:%S') $*" >&2
}

log_error() {
  echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $*" >&2
}

log_failure() {
  local pr_number="$1"
  local reason="$2"
  mkdir -p "$(dirname "$FAILURE_LOG")"
  echo "$(date '+%Y-%m-%d %H:%M:%S') PR#$pr_number FAILED: $reason" >> "$FAILURE_LOG"
}

# Check if a PR has a specific label
# Args: $1 = JSON labels array string, $2 = label name to check
has_label() {
  local labels_json="$1"
  local label_name="$2"
  echo "$labels_json" | python3 -c "
import sys, json
labels = json.load(sys.stdin)
for lbl in labels:
    if lbl.get('name', '') == '$label_name':
        sys.exit(0)
sys.exit(1)
" 2>/dev/null
}

# Check if all required checks are passing (GREEN)
# Args: $1 = PR number
all_checks_green() {
  local pr_number="$1"
  local status
  status=$(gh pr checks "$pr_number" --json name state conclusion 2>/dev/null || echo "[]")
  # If no checks at all, consider it green (no gates to pass)
  if [[ "$status" == "[]" || -z "$status" ]]; then
    return 0
  fi
  # Check that no required check has a failing conclusion
  echo "$status" | python3 -c "
import sys, json
checks = json.load(sys.stdin)
for check in checks:
    conclusion = check.get('conclusion', '')
    name = check.get('name', '')
    # FAILURE or TIMED_OUT or CANCELLED means not green
    if conclusion in ('FAILURE', 'TIMED_OUT', 'CANCELLED'):
        print(f'CHECK_FAIL: {name} = {conclusion}', file=sys.stderr)
        sys.exit(1)
    # If still PENDING or QUEUED or IN_PROGRESS, not yet green
    if conclusion in ('', None) or conclusion in ('PENDING', 'QUEUED', 'IN_PROGRESS', 'ACTION_REQUIRED'):
        print(f'CHECK_PENDING: {name} = {conclusion}', file=sys.stderr)
        sys.exit(1)
sys.exit(0)
" 2>/dev/null
}

# ── Main logic ──────────────────────────────────────────────────────────────

main() {
  log_info "Starting auto-merge scan (dry_run=$DRY_RUN, auto_mode=$AUTO_MODE, max_merges=$MAX_MERGES)"

  # Fetch all open PRs as JSON
  local prs_json
  prs_json=$(gh pr list --state open --limit 100 --json number,title,headRefName,mergeable,mergeStateStatus,labels 2>/dev/null || echo "[]")

  if [[ "$prs_json" == "[]" || -z "$prs_json" ]]; then
    log_info "No open PRs found."
    exit 0
  fi

  local merge_count=0
  local skipped_count=0
  local candidate_count=0

  # Process each PR
  local pr_count
  pr_count=$(echo "$prs_json" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")

  for (( i=0; i<pr_count; i++ )); do
    # Extract PR fields
    local pr_data
    pr_data=$(echo "$prs_json" | python3 -c "
import sys, json
prs = json.load(sys.stdin)
pr = prs[$i]
print(json.dumps(pr))
")

    local pr_number pr_title head_ref mergeable merge_state_status labels_json
    pr_number=$(echo "$pr_data" | python3 -c "import sys,json; print(json.load(sys.stdin)['number'])")
    pr_title=$(echo "$pr_data" | python3 -c "import sys,json; print(json.load(sys.stdin)['title'])")
    head_ref=$(echo "$pr_data" | python3 -c "import sys,json; print(json.load(sys.stdin)['headRefName'])")
    mergeable=$(echo "$pr_data" | python3 -c "import sys,json; print(json.load(sys.stdin).get('mergeable','UNKNOWN'))")
    merge_state_status=$(echo "$pr_data" | python3 -c "import sys,json; print(json.load(sys.stdin).get('mergeStateStatus','UNKNOWN'))")
    labels_json=$(echo "$pr_data" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin).get('labels',[])))")

    log_info "Evaluating PR#$pr_number: \"$pr_title\" (branch: $head_ref)"

    # ── Gate 1: Skip WIP / DO NOT MERGE labels ──────────────────────────
    if has_label "$labels_json" "WIP"; then
      log_info "  SKIP: PR#$pr_number has WIP label"
      (( skipped_count++ )) || true
      continue
    fi
    if has_label "$labels_json" "DO NOT MERGE"; then
      log_info "  SKIP: PR#$pr_number has DO NOT MERGE label"
      (( skipped_count++ )) || true
      continue
    fi

    # ── Gate 2: mergeable must be MERGEABLE ─────────────────────────────
    if [[ "$mergeable" != "MERGEABLE" ]]; then
      log_info "  SKIP: PR#$pr_number mergeable=$mergeable (need MERGEABLE)"
      (( skipped_count++ )) || true
      continue
    fi

    # ── Gate 3: mergeStateStatus must be CLEAN ──────────────────────────
    if [[ "$merge_state_status" != "CLEAN" ]]; then
      log_info "  SKIP: PR#$pr_number mergeStateStatus=$merge_state_status (need CLEAN)"
      (( skipped_count++ )) || true
      continue
    fi

    # ── Gate 4: All required checks must be GREEN ───────────────────────
    if ! all_checks_green "$pr_number"; then
      log_warn "  SKIP: PR#$pr_number has failing or pending checks"
      (( skipped_count++ )) || true
      continue
    fi

    # ── Gate 5: Must have auto-merge label OR match --auto pattern ──────
    local eligible=false
    if has_label "$labels_json" "auto-merge"; then
      eligible=true
      log_info "  ELIGIBLE: PR#$pr_number has 'auto-merge' label"
    elif $AUTO_MODE; then
      # Check if branch matches feat/SXX-cluster* pattern
      if [[ "$head_ref" =~ ^feat/S[0-9]+-cluster ]]; then
        eligible=true
        log_info "  ELIGIBLE: PR#$pr_number branch '$head_ref' matches feat/SXX-cluster* (--auto mode)"
      else
        log_info "  SKIP: PR#$pr_number branch '$head_ref' does not match feat/SXX-cluster* pattern"
        (( skipped_count++ )) || true
        continue
      fi
    fi

    if ! $eligible; then
      log_info "  SKIP: PR#$pr_number not eligible (no auto-merge label, --auto not set)"
      (( skipped_count++ )) || true
      continue
    fi

    # ── Max merges check ────────────────────────────────────────────────
    if [[ $MAX_MERGES -gt 0 && $merge_count -ge $MAX_MERGES ]]; then
      log_info "  SKIP: PR#$pr_number — max merges ($MAX_MERGES) reached"
      (( skipped_count++ )) || true
      continue
    fi

    # ── Merge (or dry-run) ──────────────────────────────────────────────
    (( candidate_count++ )) || true

    if $DRY_RUN; then
      log_info "  [DRY-RUN] Would merge PR#$pr_number: \"$pr_title\""
      continue
    fi

    log_info "  MERGING PR#$pr_number: \"$pr_title\""
    if gh pr merge "$pr_number" --squash --delete-branch 2>&1; then
      log_info "  SUCCESS: PR#$pr_number merged"
      (( merge_count++ )) || true
    else
      log_error "  FAILED: PR#$pr_number merge failed"
      log_failure "$pr_number" "gh pr merge --squash --delete-branch failed"
    fi
  done

  # ── Summary ─────────────────────────────────────────────────────────────
  log_info "=== Auto-merge summary ==="
  log_info "  Candidates evaluated: $candidate_count"
  log_info "  Merged:               $merge_count"
  log_info "  Skipped:              $skipped_count"
  if $DRY_RUN; then
    log_info "  (DRY-RUN mode — no PRs were actually merged)"
  fi
}

main "$@"
