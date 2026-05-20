#!/bin/sh
# iron_law_check.sh — PR gate: enforce iron-law invariants on every PR.
#
# Exit codes:
#   0 = all checks passed
#   1 = one or more violations found
#   2 = script error (missing tool, bad state)
#
# POSIX sh compatible (macOS / Linux).
#
# Checks:
#   1. No new @pytest.mark.skip or @pytest.mark.xfail without a trailing comment
#   2. No "# TODO real-data" or similar placeholder markers
#   3. collect_ignore list can only shrink, never grow
#   4. At least 1 commit in this PR (not an empty PR)

set -e

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VIOLATIONS=0

fail() {
  VIOLATIONS=$((VIOLATIONS + 1))
  printf "[IRON-LAW FAIL] %s\n" "$1" >&2
}

info() {
  printf "[IRON-LAW INFO] %s\n" "$1" >&2
}

# ---------------------------------------------------------------------------
# Determine diff range
# ---------------------------------------------------------------------------

if [ -n "${IRON_LAW_DIFF_BASE:-}" ]; then
  DIFF_BASE="${IRON_LAW_DIFF_BASE}"
elif [ -n "${GITHUB_BASE_REF:-}" ]; then
  DIFF_BASE="origin/${GITHUB_BASE_REF}"
elif [ -n "${GITHUB_EVENT_BEFORE:-}" ] && [ "${GITHUB_EVENT_BEFORE}" != "0000000000000000000000000000000000000000" ]; then
  DIFF_BASE="${GITHUB_EVENT_BEFORE}"
else
  DIFF_BASE="HEAD~1"
fi

# ---------------------------------------------------------------------------
# Check 1: No new @pytest.mark.skip / @pytest.mark.xfail without comment
# ---------------------------------------------------------------------------

check_skip_without_comment() {
  info "Checking for new @pytest.mark.skip / @pytest.mark.xfail without comment..."

  changed_files=$(git diff --name-only --diff-filter=AM "${DIFF_BASE}" -- '*.py' 2>/dev/null) || {
    info "  (no changed .py files or diff unavailable — skipping)"
    return 0
  }

  if [ -z "${changed_files}" ]; then
    info "  (no changed .py files — skipping)"
    return 0
  fi

  tmpfile=$(mktemp)

  for f in ${changed_files}; do
    if [ ! -f "${f}" ]; then
      continue
    fi
    git diff "${DIFF_BASE}" -- "${f}" 2>/dev/null | grep '^+' | grep -v '^+++' | while IFS= read -r line; do
      content=$(printf '%s' "${line}" | sed 's/^+//')
      case "${content}" in
        *@pytest.mark.skip*|*@pytest.mark.xfail*)
          case "${content}" in
            *'#'*)
              # Has a comment — OK
              ;;
            *)
              printf '%s: %s\n' "${f}" "${content}" >> "${tmpfile}"
              ;;
          esac
          ;;
      esac
    done
  done

  if [ -s "${tmpfile}" ]; then
    while IFS= read -r v; do
      fail "New skip/xfail without comment: ${v}"
    done < "${tmpfile}"
  fi

  rm -f "${tmpfile}"
}

# ---------------------------------------------------------------------------
# Check 2: No "# TODO real-data" or similar placeholder markers
# ---------------------------------------------------------------------------

check_placeholder_markers() {
  info "Checking for placeholder markers (TODO real-data, etc.)..."

  changed_files=$(git diff --name-only --diff-filter=AM "${DIFF_BASE}" 2>/dev/null) || {
    info "  (no changed files or diff unavailable — skipping)"
    return 0
  }

  if [ -z "${changed_files}" ]; then
    info "  (no changed files — skipping)"
    return 0
  fi

  tmpfile=$(mktemp)

  for f in ${changed_files}; do
    if [ ! -f "${f}" ]; then
      continue
    fi
    git diff "${DIFF_BASE}" -- "${f}" 2>/dev/null | grep '^+' | grep -v '^+++' | while IFS= read -r line; do
      content=$(printf '%s' "${line}" | sed 's/^+//')
      lower_content=$(printf '%s' "${content}" | tr '[:upper:]' '[:lower:]')
      case "${lower_content}" in
        *'todo real-data'*|*'todo: real-data'*|*'todo real_data'*|*'placeholder'*|*'fake-data'*|*'mock-data'*)
          printf '%s: %s\n' "${f}" "${content}" >> "${tmpfile}"
          ;;
      esac
    done
  done

  if [ -s "${tmpfile}" ]; then
    while IFS= read -r v; do
      fail "Placeholder marker found: ${v}"
    done < "${tmpfile}"
  fi

  rm -f "${tmpfile}"
}

# ---------------------------------------------------------------------------
# Check 3: collect_ignore can only shrink, never grow
# ---------------------------------------------------------------------------

check_collect_ignore() {
  info "Checking collect_ignore list (must not grow)..."

  # Find all conftest.py files that changed (git doesn't support ** glob in pathspec)
  changed_conftest=$(git diff --name-only --diff-filter=AM "${DIFF_BASE}" 2>/dev/null | grep 'conftest\.py$') || {
    info "  (no changed conftest.py — skipping)"
    return 0
  }

  if [ -z "${changed_conftest}" ]; then
    info "  (no changed conftest.py — skipping)"
    return 0
  fi

  for f in ${changed_conftest}; do
    if [ ! -f "${f}" ]; then
      continue
    fi

    base_list=$(git show "${DIFF_BASE}:${f}" 2>/dev/null | python3 -c "
import sys, re
text = sys.stdin.read()
match = re.search(r'collect_ignore\s*=\s*\[(.*?)\]', text, re.DOTALL)
if not match:
    sys.exit(0)
body = match.group(1)
items = re.findall(r'[\"'\"'\"']([^\"'\"'\"']+)[\"'\"'\"']', body)
for item in items:
    print(item)
" 2>/dev/null || true)

    current_list=$(python3 -c "
import re
with open('${f}', 'r') as fh:
    text = fh.read()
match = re.search(r'collect_ignore\s*=\s*\[(.*?)\]', text, re.DOTALL)
if not match:
    sys.exit(0)
body = match.group(1)
items = re.findall(r'[\"'\"'\"']([^\"'\"'\"']+)[\"'\"'\"']', body)
for item in items:
    print(item)
" 2>/dev/null || true)

    base_count=0
    if [ -n "${base_list}" ]; then
      base_count=$(printf '%s\n' "${base_list}" | wc -l | tr -d ' ')
    fi
    current_count=0
    if [ -n "${current_list}" ]; then
      current_count=$(printf '%s\n' "${current_list}" | wc -l | tr -d ' ')
    fi

    if [ "${current_count}" -gt "${base_count}" ]; then
      added_items=""
      for item in ${current_list}; do
        found=0
        for bitem in ${base_list}; do
          if [ "${item}" = "${bitem}" ]; then
            found=1
            break
          fi
        done
        if [ "${found}" -eq 0 ]; then
          added_items="${added_items} ${item}"
        fi
      done
      fail "collect_ignore in ${f} grew from ${base_count} to ${current_count} entries (added:${added_items})"
    else
      info "  collect_ignore in ${f}: ${base_count} -> ${current_count} (OK, did not grow)"
    fi
  done
}

# ---------------------------------------------------------------------------
# Check 4: At least 1 commit in this PR
# ---------------------------------------------------------------------------

check_not_empty_pr() {
  info "Checking PR is not empty (at least 1 commit)..."

  if [ -n "${GITHUB_EVENT_NAME:-}" ] && [ "${GITHUB_EVENT_NAME}" = "push" ]; then
    info "  (push event — has commits)"
    return 0
  fi

  commit_count=$(git rev-list --count "${DIFF_BASE}..HEAD" 2>/dev/null) || {
    info "  (unable to count commits — assuming OK)"
    return 0
  }

  if [ "${commit_count}" -lt 1 ]; then
    fail "Empty PR: no commits between ${DIFF_BASE} and HEAD"
  else
    info "  ${commit_count} commit(s) found — OK"
  fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
  if ! command -v git >/dev/null 2>&1; then
    printf "[IRON-LAW ERROR] git not found in PATH\n" >&2
    exit 2
  fi

  if ! git rev-parse --git-dir >/dev/null 2>&1; then
    printf "[IRON-LAW ERROR] not a git repository\n" >&2
    exit 2
  fi

  check_skip_without_comment
  check_placeholder_markers
  check_collect_ignore
  check_not_empty_pr

  if [ "${VIOLATIONS}" -gt 0 ]; then
    printf "\n[IRON-LAW] %d violation(s) found — PR blocked.\n" "${VIOLATIONS}" >&2
    exit 1
  fi

  info "All iron-law checks passed."
  exit 0
}

main "$@"
