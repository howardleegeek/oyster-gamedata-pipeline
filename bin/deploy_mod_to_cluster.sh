#!/usr/bin/env bash
# deploy_mod_to_cluster.sh — stage the latest D16 server mod jar into each
# cluster Paper instance's mods/ directory. NEVER restarts servers — that
# is Howard's call (swarm controller "不停运转"). After this script runs,
# Howard manually restarts each Paper using the procedure documented in
# bin/deploy_mod_to_cluster_RESTART.md.
#
# What it does (idempotent, fail-soft):
#   1. Downloads the latest oyster-recorder-mod-*.jar from the most recent
#      successful "Build MC Fabric Mod" GHA run via `gh run download`.
#   2. For each cluster Paper dir:
#        - Confirms it's a Fabric server (libraries/net/fabricmc/fabric-loader/
#          OR a fabric-server-launch.jar in the dir root). If neither
#          exists, it's still vanilla Paper — error out for THAT dir,
#          continue with the others, and exit non-zero at the end.
#        - Removes any older oyster-recorder-mod-*.jar in mods/ (preserves
#          all other mods like Sodium, Lithium, etc.).
#        - Copies the new jar in.
#        - Looks up the lsof LISTEN port for the running Paper PID (read
#          from <paper_dir>/paper.pid) and prints a restart-instruction
#          line for that specific instance.
#   3. Exits 0 only if every dir succeeded; otherwise exits with the count
#      of failures so the caller (and `tests/`) can detect partial failure.
#
# USAGE:
#   bin/deploy_mod_to_cluster.sh                    # default cluster dirs
#   PAPER_DIRS="/tmp/foo /tmp/bar" bin/deploy_mod_to_cluster.sh
#   MOD_JAR_OVERRIDE=/path/to/mod.jar bin/deploy_mod_to_cluster.sh
#                                                   # skip GHA download
#
# ENVIRONMENT:
#   PAPER_DIRS           Space-separated list of Paper server dirs.
#                        Default: the three live cluster dirs.
#   MOD_JAR_OVERRIDE     If set to an existing file, skip `gh run download`
#                        and use that jar. Used by tests + manual flows.
#   GH_WORKFLOW_NAME     GHA workflow name (default "Build MC Fabric Mod").
#   DOWNLOAD_DIR         Where to stage the downloaded jar (default
#                        $(mktemp -d)).
#
# EXIT CODES:
#   0   every Paper dir got the new jar
#   N   N Paper dirs failed (e.g. not Fabric, missing mods/ writeable)
#   2   couldn't fetch a mod jar at all (no GHA artifact, no override)
#   3   `gh` CLI missing and no MOD_JAR_OVERRIDE provided
#
# Howard 2026-05-07 (D16 follow-up): the cluster's three Paper instances
# at ports 25565/25566/25567 are still vanilla Paper. They need to be
# converted to Fabric servers (paper.jar -> fabric-server-launch.jar)
# manually first; this script ONLY stages the mod jar. See the companion
# RESTART.md for the full restart procedure.

set -uo pipefail
# NOTE: deliberately NOT using `set -e` — we want per-dir failures to be
# fail-soft (continue with the rest), not abort the whole script.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# -- defaults ---------------------------------------------------------------
DEFAULT_PAPER_DIRS=(
    /tmp/oyster_paper_d3
    /tmp/oyster_paper_mac1_p2
    /tmp/oyster_paper_mac1_p3
)
PAPER_DIRS_INPUT="${PAPER_DIRS:-${DEFAULT_PAPER_DIRS[*]}}"
read -r -a TARGET_DIRS <<< "$PAPER_DIRS_INPUT"

GH_WORKFLOW_NAME="${GH_WORKFLOW_NAME:-Build MC Fabric Mod}"
DOWNLOAD_DIR="${DOWNLOAD_DIR:-}"
MOD_JAR_OVERRIDE="${MOD_JAR_OVERRIDE:-}"

log() {
    printf '[deploy_mod] %s\n' "$*" >&2
}

err() {
    printf '[deploy_mod] ERROR: %s\n' "$*" >&2
}

# -- step 1: obtain the mod jar --------------------------------------------
# Either (a) MOD_JAR_OVERRIDE is set to an existing file, or (b) we shell
# out to `gh run download` for the latest successful workflow run.
obtain_mod_jar() {
    if [[ -n "$MOD_JAR_OVERRIDE" ]]; then
        if [[ ! -f "$MOD_JAR_OVERRIDE" ]]; then
            err "MOD_JAR_OVERRIDE set but file not found: $MOD_JAR_OVERRIDE"
            return 2
        fi
        log "using MOD_JAR_OVERRIDE: $MOD_JAR_OVERRIDE"
        printf '%s\n' "$MOD_JAR_OVERRIDE"
        return 0
    fi

    if ! command -v gh >/dev/null 2>&1; then
        err "gh CLI not installed and no MOD_JAR_OVERRIDE provided. Either install gh or set MOD_JAR_OVERRIDE=/path/to/jar."
        return 3
    fi

    if [[ -z "$DOWNLOAD_DIR" ]]; then
        DOWNLOAD_DIR="$(mktemp -d -t deploy_mod_XXXXXX)"
    fi
    mkdir -p "$DOWNLOAD_DIR"

    log "looking up most recent successful '$GH_WORKFLOW_NAME' workflow run..."
    # `--json databaseId,conclusion,name -L 50` lists the last 50 runs in
    # JSON; pick the first SUCCESS run with the right name.
    local run_id
    run_id=$(gh run list \
        --workflow="$GH_WORKFLOW_NAME" \
        --status=success \
        --limit=1 \
        --json=databaseId \
        --jq='.[0].databaseId' 2>/dev/null || true)

    if [[ -z "$run_id" || "$run_id" == "null" ]]; then
        err "no successful '$GH_WORKFLOW_NAME' run found via gh. Run the workflow first or set MOD_JAR_OVERRIDE."
        return 2
    fi
    log "downloading artifacts from run $run_id into $DOWNLOAD_DIR"

    if ! gh run download "$run_id" --dir "$DOWNLOAD_DIR" >/dev/null 2>&1; then
        err "gh run download failed for run $run_id"
        return 2
    fi

    # Find the produced jar. Artifact name pattern (per build-mc-mod.yml):
    # oyster-recorder-mod-mc<MC_VERSION>/oyster-recorder-mod-*-mc<MC_VERSION>.jar
    local jar
    jar=$(find "$DOWNLOAD_DIR" -name 'oyster-recorder-mod-*.jar' -not -name '*-sources.jar' | head -1)
    if [[ -z "$jar" ]]; then
        err "no oyster-recorder-mod-*.jar found under $DOWNLOAD_DIR after gh run download"
        return 2
    fi
    log "found mod jar: $jar ($(du -h "$jar" 2>/dev/null | cut -f1))"
    printf '%s\n' "$jar"
}

# -- step 2: verify a Paper dir is a Fabric server -------------------------
# Real Fabric servers have BOTH:
#   - libraries/net/fabricmc/fabric-loader/<version>/  (loader jar tree)
#   - fabric-server-launch.jar  (the launcher in the dir root)
# We accept EITHER signal as "Fabric is here" since tester setups vary —
# some installers drop only the launcher, others only the loader libs.
# Vanilla Paper has neither.
is_fabric_server() {
    local paper_dir="$1"
    [[ -d "$paper_dir/libraries/net/fabricmc/fabric-loader" ]] && return 0
    [[ -f "$paper_dir/fabric-server-launch.jar" ]] && return 0
    return 1
}

# -- step 3: drop the jar (clobber-only-Oyster) ----------------------------
# Removes any pre-existing oyster-recorder-mod-*.jar in mods/ but
# preserves every other jar (Sodium, Lithium, etc.). Copies new jar in.
# Idempotent: if the destination jar already has identical bytes, no-op.
drop_mod_jar() {
    local paper_dir="$1"
    local src_jar="$2"

    local mods_dir="$paper_dir/mods"
    mkdir -p "$mods_dir" || {
        err "could not create $mods_dir"
        return 1
    }

    local jar_basename
    jar_basename=$(basename "$src_jar")
    local dest="$mods_dir/$jar_basename"

    # Idempotency: same bytes already there → no-op.
    if [[ -f "$dest" ]] && cmp -s "$src_jar" "$dest" 2>/dev/null; then
        log "  [$paper_dir] already has $jar_basename (identical bytes) — no-op"
        return 0
    fi

    # Remove any older oyster-recorder-mod-*.jar (NOT the new dest yet,
    # since cp will overwrite it cleanly anyway). Preserves all non-Oyster
    # mods.
    local removed=0
    while IFS= read -r -d '' old_jar; do
        if [[ "$(basename "$old_jar")" != "$jar_basename" ]]; then
            rm -f "$old_jar" && removed=$((removed + 1))
        fi
    done < <(find "$mods_dir" -maxdepth 1 -name 'oyster-recorder-mod-*.jar' -print0 2>/dev/null)

    if (( removed > 0 )); then
        log "  [$paper_dir] removed $removed older oyster-recorder-mod jar(s)"
    fi

    # Copy new jar in.
    cp "$src_jar" "$dest" || {
        err "  [$paper_dir] cp failed"
        return 1
    }

    log "  [$paper_dir] staged $jar_basename"
    return 0
}

# -- step 4: lookup running port for a Paper dir ---------------------------
# The Paper dir contains paper.pid (written by paper_server_start.sh). We
# use lsof to find the LISTEN port for that PID. If lsof or pid is
# unavailable, return "unknown" — the restart instruction still gets
# printed.
lookup_port() {
    local paper_dir="$1"
    local pid_file="$paper_dir/paper.pid"
    if [[ ! -f "$pid_file" ]]; then
        printf 'unknown (no paper.pid in %s)\n' "$paper_dir"
        return
    fi
    local pid
    pid=$(tr -d '[:space:]' < "$pid_file")
    if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
        printf 'unknown (PID %s not running)\n' "${pid:-<empty>}"
        return
    fi
    if ! command -v lsof >/dev/null 2>&1; then
        printf 'unknown (lsof not installed; PID %s is alive)\n' "$pid"
        return
    fi
    local port
    port=$(lsof -nP -p "$pid" -iTCP -sTCP:LISTEN 2>/dev/null \
        | awk 'NR>1 && $9 ~ /:[0-9]+$/ {sub(/.*:/, "", $9); print $9; exit}')
    if [[ -n "$port" ]]; then
        printf '%s (PID %s)\n' "$port" "$pid"
    else
        printf 'unknown (PID %s alive but no LISTEN port)\n' "$pid"
    fi
}

# -- step 5: per-dir deploy ------------------------------------------------
deploy_one() {
    local paper_dir="$1"
    local src_jar="$2"

    log "deploying to $paper_dir"

    if [[ ! -d "$paper_dir" ]]; then
        err "  [$paper_dir] directory does not exist — skipping"
        return 1
    fi

    if ! is_fabric_server "$paper_dir"; then
        err "  [$paper_dir] is NOT a Fabric server (no libraries/net/fabricmc/fabric-loader/ AND no fabric-server-launch.jar). Convert paper.jar -> fabric-server-launch.jar first; see bin/deploy_mod_to_cluster_RESTART.md."
        return 1
    fi

    if ! drop_mod_jar "$paper_dir" "$src_jar"; then
        err "  [$paper_dir] drop_mod_jar failed"
        return 1
    fi

    local port_info
    port_info=$(lookup_port "$paper_dir")
    log "  [$paper_dir] to activate, restart this Paper instance on port $port_info — see bin/deploy_mod_to_cluster_RESTART.md"
    return 0
}

# -- main ------------------------------------------------------------------
main() {
    log "PAPER_DIRS = ${TARGET_DIRS[*]}"
    log "obtaining mod jar..."

    # Note on quirk: `local var=$(cmd)` masks $? because `local` itself
    # always succeeds. We declare first, assign second, and capture $?
    # *immediately* on its own line.
    local src_jar
    src_jar=$(obtain_mod_jar)
    local rc=$?
    if (( rc != 0 )); then
        err "could not obtain mod jar (exit $rc) — aborting"
        return "$rc"
    fi

    local failures=0
    for d in "${TARGET_DIRS[@]}"; do
        deploy_one "$d" "$src_jar" || failures=$((failures + 1))
        # Always continue — fail-soft per-dir.
    done

    if (( failures == 0 )); then
        log "ALL ${#TARGET_DIRS[@]} dir(s) staged successfully."
        log "Next: SSH to each host (or do it locally) and follow"
        log "      bin/deploy_mod_to_cluster_RESTART.md to bounce the servers."
        log "      The swarm controller is intentionally NOT restarted by"
        log "      this script (Howard's call — '不停运转')."
        return 0
    fi

    err "$failures of ${#TARGET_DIRS[@]} dir(s) failed. See errors above."
    return "$failures"
}

main "$@"
