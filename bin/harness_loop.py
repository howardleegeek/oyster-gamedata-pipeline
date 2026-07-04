#!/usr/bin/env python3
"""
bin/harness_loop.py — Long-running autonomous harness daemon.

Eliminates human-in-the-loop. Reads docs/audit_gaps.yaml, picks atomic
non-overlapping pending gaps, auto-generates spec.md, dispatches to
mac-2 cluster via minimax_agent_simple.py, polls until done, scp's outputs
back, verifies syntax, commits + pushes 1-by-1, updates gap status.

Loop forever (or until all gaps done). Howard checks in once a day,
sees commits streaming into main.

Run:
    nohup python3 bin/harness_loop.py > harness.log 2>&1 &

Stop:
    pkill -f 'python3 bin/harness_loop.py'

Architecture:
    docs/audit_gaps.yaml (registry, status state machine)
    │
    ▼
    pick_pending_gaps() → top N atomic, non-overlapping by file
    │
    ▼
    generate_spec(gap) → /tmp/harness_specs/<id>/spec.md
    │
    ▼
    dispatch_to_mac2() → scp + nohup minimax_agent_simple.py
    │
    ▼
    poll_completion() → grep 'TASK RESULT: completed' / live count
    │
    ▼
    collect_and_verify() → scp + AST/bash check + size sanity
    │
    ▼
    commit_and_push() → 1 commit per gap, idempotent
    │
    ▼
    update_status() → gap.status = completed | failed
    │
    ▼ sleep 60s, loop

State invariants:
    - dispatched gaps survive harness restart (re-attach on registry status)
    - failed gaps marked with reason + retry counter (max 3 retries)
    - completed gaps idempotent (skip if already in git)
    - never edit existing files (cluster truncates) — only NEW files allowed
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
GAPS_YAML = REPO_ROOT / "docs" / "audit_gaps.yaml"
SPEC_DIR = Path("/tmp/harness_specs")
MAC2_HOST = "howard-mac2"
MAC2_WORK = "/Users/howardlee/aliyun_work"
MINIMAX = f"{MAC2_WORK}/minimax_agent_simple.py"
MAX_PARALLEL = 8           # mac-2 stable concurrency
POLL_INTERVAL_S = 60       # check every 60s
MAX_RETRIES = 3
SLEEP_BETWEEN_LOOPS_S = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(REPO_ROOT / "harness.log"),
    ],
)
log = logging.getLogger("harness")


# ----------------------------------------------------------------- yaml IO
def load_gaps() -> dict[str, Any]:
    """Robust YAML loader.

    Disaster-proofing layers:
      1. Try strict yaml.safe_load.
      2. On YAMLError: log loudly + auto-revert via `git checkout HEAD -- docs/audit_gaps.yaml`
         (last-good is always in git since save_gaps commits every iteration), then retry.
      3. On second failure: return {} — the iteration becomes a no-op so the daemon stays
         up and the watchdog can keep heartbeating instead of crash-looping.
      4. PyYAML missing: fall back to minimal parser for bootstrap.
    """
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        return _parse_minimal_yaml(GAPS_YAML.read_text())

    try:
        return yaml.safe_load(GAPS_YAML.read_text()) or {}
    except yaml.YAMLError as e:
        log.error(f"YAML poisoned in {GAPS_YAML}: {e!r} — attempting auto-revert from HEAD")
        try:
            run(["git", "-C", str(REPO_ROOT), "checkout", "HEAD", "--", "docs/audit_gaps.yaml"])
            return yaml.safe_load(GAPS_YAML.read_text()) or {}
        except Exception as e2:
            log.error(f"Auto-revert failed: {e2!r}; returning empty state to keep daemon alive")
            return {}


def save_gaps(data: dict[str, Any]) -> None:
    """Validated YAML writer with 3-way merge (ISC-11, 2026-05-04).

    Layered safety:
      1. ALWAYS git pull right before save so we have the freshest state.
      2. Re-load disk YAML; for any gap.id we don't know about, ADD IT to
         our in-memory data (someone added it concurrently while we worked).
      3. For any gap.id we both know, OUR daemon-maintained fields
         (status / dispatched_at / completed_at / fail_reason / retries)
         override theirs; everything else preserves disk state.
      4. Write via yaml.safe_dump (guaranteed parseable).
      5. Round-trip parse check; if poisoned, restore HEAD.

    Why this exists: 2026-05-04 the daemon stomped 22+22+22 specs across 3
    separate commits because save_gaps overwrote the working tree with a
    pre-pull snapshot. Three-way merge fixes the entire failure class.
    """
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        log.warning("PyYAML missing; skipping write — install with `pip install pyyaml`")
        return

    # Step 1: pull latest disk state (concurrent commits may have added gaps)
    try:
        run(["git", "-C", str(REPO_ROOT), "pull", "--rebase", "--autostash",
             "origin", "main"], check=False, timeout=30)
    except Exception as e:
        log.warning(f"  save_gaps pull-before-write skipped: {e}")

    # Step 2: 3-way merge — preserve concurrent gap additions
    DAEMON_OWNED_FIELDS = {"status", "dispatched_at", "completed_at",
                            "fail_reason", "retries", "skip_reason"}
    try:
        if GAPS_YAML.exists():
            disk = yaml.safe_load(GAPS_YAML.read_text()) or {}
            disk_gaps = {g["id"]: g for g in disk.get("gaps", []) if "id" in g}
            our_gaps = {g["id"]: g for g in data.get("gaps", []) if "id" in g}

            # New on disk that we don't know → add to our state
            for gid, dg in disk_gaps.items():
                if gid not in our_gaps:
                    data.setdefault("gaps", []).append(dg)

            # Common: our daemon-owned fields win, everything else from disk wins
            merged_gaps = []
            for g in data.get("gaps", []):
                gid = g.get("id")
                if gid in disk_gaps:
                    merged = dict(disk_gaps[gid])
                    for k in DAEMON_OWNED_FIELDS:
                        if k in g:
                            merged[k] = g[k]
                        elif k in disk_gaps[gid]:
                            merged[k] = disk_gaps[gid][k]
                    merged_gaps.append(merged)
                else:
                    merged_gaps.append(g)
            data["gaps"] = merged_gaps
    except yaml.YAMLError as e:
        log.warning(f"  3-way merge skipped (disk YAML invalid): {e}")

    # Step 3: write + validate
    out = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    GAPS_YAML.write_text(out)
    try:
        yaml.safe_load(out)
    except yaml.YAMLError as e:
        log.error(f"save_gaps produced unparseable YAML?? {e!r} — restoring from HEAD")
        run(["git", "-C", str(REPO_ROOT), "checkout", "HEAD", "--", "docs/audit_gaps.yaml"])


def _parse_minimal_yaml(text: str) -> dict[str, Any]:
    """Tiny YAML subset parser for bootstrap when PyYAML missing."""
    out: dict[str, Any] = {"gaps": []}
    cur: dict[str, Any] | None = None
    for line in text.splitlines():
        if line.startswith("  - id:"):
            if cur:
                out["gaps"].append(cur)
            cur = {"id": line.split(":", 1)[1].strip()}
        elif line.startswith("    ") and cur is not None and ":" in line:
            k, v = line.strip().split(":", 1)
            cur[k.strip()] = v.strip()
        elif line.startswith("version:"):
            out["version"] = int(line.split(":", 1)[1].strip())
    if cur:
        out["gaps"].append(cur)
    return out


# ----------------------------------------------------------------- gap picker
def pick_pending_gaps(gaps: list[dict], limit: int = MAX_PARALLEL) -> list[dict]:
    """Return up to `limit` pending gaps, sorted by priority (P0>P1>P2>P3),
    skipping any whose 'title' file conflicts with another in the batch."""
    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    candidates = [g for g in gaps if g.get("status", "pending") == "pending"]
    candidates.sort(key=lambda g: (priority_order.get(g.get("priority", "P3"), 9), g["id"]))

    picked: list[dict] = []
    seen_files: set[str] = set()
    for g in candidates:
        title = g.get("title", "")
        if title in seen_files:
            continue
        # Guarantee 1 NEW file (skip if file already exists in repo)
        full = REPO_ROOT / title
        if full.exists():
            log.info(f"  skip {g['id']}: {title} already exists")
            g["status"] = "skipped"
            g["skip_reason"] = "file already in repo"
            continue
        seen_files.add(title)
        picked.append(g)
        if len(picked) >= limit:
            break
    return picked


# ----------------------------------------------------------------- spec generator
def generate_spec(gap: dict) -> str:
    """Template a minimal-but-complete spec.md from a gap entry.

    Spec is intentionally simple: 1 NEW file, no edits, no dependencies.
    Cluster reliably handles this (vs the rc6 disaster of edit-existing).
    """
    return f"""# {gap['id']} · {gap['title']}

## Purpose
{gap.get('purpose', 'Production-grade vendor pipeline component')}.

## Constraints
- ONLY write 1 NEW file: `{gap['title']}`
- NO edits to existing files (harness rule — cluster truncates large edits)
- NO external runtime deps that vendors don't already have
  (stdlib + numpy/PIL/PyYAML/openpyxl OK; pydantic/torch must be lazy import)
- Target line count: {gap.get('lines_estimate', 100)} (±50% acceptable)
- Type hints + docstrings + module-level header

## Implementation
- Read PDF spec at docs/PRD.md / docs/BUYER_SPEC_V1.md if relevant
- For Python: include `def main(argv) -> int` with argparse CLI
- For bash: `set -euo pipefail` + EXIT trap + cleanup
- For YAML/MD: validate parseable / valid markdown structure

## Quality bar
- AST/syntax must pass (Python: ast.parse, bash: bash -n, YAML: yaml.safe_load)
- No `subprocess.run(..., shell=True)` (use list form)
- No hardcoded /tmp/ paths (use tempfile.mkdtemp)
- No hardcoded credentials / API keys

## Submit
1. write_file('{gap['title']}', ...)
2. run_cmd to verify syntax (e.g. `python3 -c 'import ast; ast.parse(open(\"...\").read())'`)
3. finish

NO need to write tests for this gap — main repo CI runs them separately.
"""


# ----------------------------------------------------------------- mac-2 dispatch
import socket as _net

# When this daemon runs ON mac-2 itself, skip ssh — operate directly on local fs.
IS_LOCAL_TO_MAC2 = (
    "Howards-MacBook-Pro-2" in _net.gethostname()
    or os.environ.get("HARNESS_LOCAL_MAC2") == "1"
)


def run(cmd: list[str], check: bool = True, capture: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=capture, text=True, timeout=timeout)


def ssh_run(remote_cmd: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a command on mac-2 cluster. If we're already on mac-2, skip ssh."""
    if IS_LOCAL_TO_MAC2:
        return run(["bash", "-lc", remote_cmd], check=False, timeout=timeout)
    return run(["ssh", MAC2_HOST, remote_cmd], check=False, timeout=timeout)


def scp_to(local: Path, remote: str) -> None:
    """Copy local file to mac-2 path. If already on mac-2, just cp."""
    if IS_LOCAL_TO_MAC2:
        Path(remote).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(str(local), remote)
        return
    run(["scp", "-q", str(local), f"{MAC2_HOST}:{remote}"], timeout=120)


def scp_from(remote: str, local: Path) -> None:
    """Copy file FROM mac-2 to local. If already on mac-2, cp from path."""
    local.parent.mkdir(parents=True, exist_ok=True)
    if IS_LOCAL_TO_MAC2:
        if Path(remote).exists():
            shutil.copy(remote, str(local))
        return
    run(["scp", "-q", f"{MAC2_HOST}:{remote}", str(local)], timeout=120)


def dispatch(gap: dict) -> bool:
    """scp spec to mac-2 + launch nohup minimax_agent_simple.py.

    Wrapped in try/except so transient ssh/scp failures don't crash the
    whole daemon — gap stays 'pending' and gets re-tried next iteration.
    Increased timeout to 180s for ssh dispatch (was 60s, mac-2 sometimes
    slow when busy with prior wave).
    """
    gap_id = gap["id"]
    try:
        spec = SPEC_DIR / gap_id / "spec.md"
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text(generate_spec(gap))

        remote_dir = f"{MAC2_WORK}/{gap_id}"
        ssh_run(f"mkdir -p {remote_dir}", timeout=120)
        scp_to(spec, f"{remote_dir}/spec.md")
        cmd = (
            f"cd {remote_dir} && "
            f"SPEC_FILE=spec.md WORKING_DIR=. TASK_ID={gap_id} "
            f"nohup python3 {MINIMAX} > agent.log 2>&1 & "
            f"echo $!"
        )
        res = ssh_run(cmd, timeout=180)
        if res.returncode != 0:
            log.error(f"  dispatch failed for {gap_id}: {res.stderr[:200]}")
            return False
        log.info(f"  → {gap_id} dispatched (PID={res.stdout.strip()})")
        gap["status"] = "dispatched"
        gap["dispatched_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        return True
    except subprocess.TimeoutExpired as e:
        log.error(f"  dispatch TIMEOUT for {gap_id}: {e}")
        return False
    except subprocess.CalledProcessError as e:
        log.error(f"  dispatch CALLED-ERR for {gap_id}: {e}")
        return False
    except Exception as e:
        log.error(f"  dispatch unexpected error for {gap_id}: {e}")
        return False


# ----------------------------------------------------------------- collection
def is_completed(gap_id: str) -> bool:
    res = ssh_run(f"grep -q 'TASK RESULT: completed' {MAC2_WORK}/{gap_id}/agent.log && echo YES || echo NO")
    return "YES" in res.stdout


def is_running(gap_id: str) -> bool:
    res = ssh_run(f"pgrep -f 'TASK_ID={gap_id}' | head -1")
    return bool(res.stdout.strip())


def collect_artifact(gap: dict) -> Path | None:
    """scp the produced file from mac-2 to local repo path."""
    gap_id = gap["id"]
    target_path = REPO_ROOT / gap["title"]
    remote_path = f"{MAC2_WORK}/{gap_id}/{gap['title']}"
    try:
        scp_from(remote_path, target_path)
    except subprocess.CalledProcessError as e:
        log.error(f"  scp failed for {gap_id}: {e}")
        return None
    if not target_path.exists() or target_path.stat().st_size < 50:
        log.error(f"  {gap_id} artifact too small or missing")
        return None
    return target_path


def verify_syntax(path: Path, gap: dict | None = None) -> bool:
    """AST / bash -n / yaml.safe_load based on extension.

    Plus truncation guard (added 2026-05-04): cluster sometimes writes a
    5-line stub of a 200-line spec (rc6 disaster). For .py / .sh files we
    require >= max(40, lines_estimate * 0.4) lines so a truncation can't
    sneak past pure ast.parse.
    """
    suffix = path.suffix
    try:
        text = path.read_text()
        if suffix == ".py":
            import ast
            ast.parse(text)
        elif suffix == ".sh" or suffix == ".bash":
            run(["bash", "-n", str(path)])
        elif suffix in {".yml", ".yaml"}:
            import yaml
            yaml.safe_load(text)
        elif suffix == ".md":
            if len(text) < 100:  # too small to be a real doc
                return False

        # Truncation guard: code files (.py / .sh / .bash) must hit a sane
        # line floor relative to the spec's lines_estimate.
        if suffix in {".py", ".sh", ".bash"}:
            n_lines = len(text.splitlines())
            estimate = (gap or {}).get("lines_estimate", 0) or 0
            floor = max(40, int(estimate * 0.4))  # 40 LOC absolute, or 40% of estimate
            if n_lines < floor:
                log.error(
                    f"  truncation guard tripped: {path} has {n_lines} LOC, "
                    f"floor={floor} (estimate={estimate})"
                )
                return False
        return True
    except Exception as e:
        log.error(f"  syntax check failed for {path}: {e}")
        return False


def commit_and_push(gap: dict, artifact: Path) -> bool:
    """git add + commit + push for this single artifact. 1 spec = 1 commit."""
    rel = artifact.relative_to(REPO_ROOT)
    msg = (
        f"feat(harness:{gap['id']}): {gap['title']} (auto-dispatched)\n\n"
        f"{gap.get('purpose', '')}\n\n"
        f"Auto-generated by bin/harness_loop.py from docs/audit_gaps.yaml.\n"
        f"Cluster: mac-2 minimax_agent_simple.py.\n"
        f"Priority: {gap.get('priority', 'P?')}"
    )
    try:
        run(["git", "-C", str(REPO_ROOT), "add", str(rel)])
        run(["git", "-C", str(REPO_ROOT), "commit", "-m", msg])
        run(["git", "-C", str(REPO_ROOT), "push", "origin", "main"], timeout=120)
        log.info(f"  ✓ committed + pushed {gap['id']}: {rel}")
        return True
    except subprocess.CalledProcessError as e:
        log.error(f"  git commit/push failed for {gap['id']}: {e.stderr or e}")
        return False


# ----------------------------------------------------------------- failover lock
import socket as _socket

LOCK_TTL_S = 180  # heartbeat older than this → other instance considered dead
HOSTNAME = _socket.gethostname()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _parse_iso(s: str) -> float:
    try:
        return time.mktime(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))
    except Exception as e:
        log.debug("Failed to parse ISO timestamp %r: %s", s, e)
        return 0.0


def acquire_or_check_lock(data: dict) -> bool:
    """Check audit_gaps.yaml top-level harness_lock field. Returns True if
    THIS host owns/can-claim the lock. Returns False if another host has
    a fresh heartbeat (we should exit gracefully).

    Heartbeat freshness: < LOCK_TTL_S seconds = active, else stale (claim).
    """
    lock = data.get("harness_lock", {}) or {}
    cur_host = lock.get("host", "")
    cur_hb = _parse_iso(lock.get("last_heartbeat", ""))
    age = time.time() - cur_hb if cur_hb else 9e9

    if cur_host == HOSTNAME:
        return True   # we are the owner; refresh heartbeat
    if cur_host and age < LOCK_TTL_S:
        log.warning(f"Lock held by {cur_host} (age={int(age)}s, ttl={LOCK_TTL_S}s); exiting.")
        return False
    log.info(f"Claiming lock from {cur_host or '<none>'} (stale age={int(age)}s)")
    data["harness_lock"] = {
        "host": HOSTNAME,
        "pid": os.getpid(),
        "last_heartbeat": _now_iso(),
    }
    return True


def refresh_heartbeat(data: dict) -> None:
    data.setdefault("harness_lock", {})
    data["harness_lock"]["host"] = HOSTNAME
    data["harness_lock"]["pid"] = os.getpid()
    data["harness_lock"]["last_heartbeat"] = _now_iso()


def git_pull() -> bool:
    """Pull latest audit_gaps.yaml from origin. Idempotent."""
    try:
        run(["git", "-C", str(REPO_ROOT), "pull", "--rebase", "origin", "main"], timeout=60)
        return True
    except subprocess.CalledProcessError as e:
        log.error(f"  git pull failed: {e}")
        return False


# ----------------------------------------------------------------- main loop
def harness_loop(once: bool = False, dry_run: bool = False) -> int:
    SPEC_DIR.mkdir(parents=True, exist_ok=True)
    iteration = 0
    log.info(f"Harness starting on host={HOSTNAME} pid={os.getpid()}")

    while True:
        iteration += 1
        log.info(f"=== Harness iteration {iteration} (host={HOSTNAME}) ===")

        # Failover: pull latest state, check lock
        if not dry_run:
            git_pull()

        data = load_gaps()
        if not dry_run and not acquire_or_check_lock(data):
            log.info("Another host owns the lock — exiting cleanly")
            return 0

        gaps_list = data.get("gaps", [])

        # Step 1: collect any 'dispatched' that are now completed
        for g in gaps_list:
            if g.get("status") == "dispatched" and is_completed(g["id"]):
                log.info(f"COMPLETING {g['id']}: {g['title']}")
                if dry_run:
                    g["status"] = "completed"
                    continue
                art = collect_artifact(g)
                if art is None:
                    g["status"] = "failed"
                    g["fail_reason"] = "artifact missing/too small"
                    g["retries"] = g.get("retries", 0) + 1
                    continue
                if not verify_syntax(art, gap=g):
                    g["status"] = "failed"
                    g["fail_reason"] = "syntax check failed (or truncation)"
                    g["retries"] = g.get("retries", 0) + 1
                    continue
                if commit_and_push(g, art):
                    g["status"] = "completed"
                    g["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    g["status"] = "failed"
                    g["fail_reason"] = "git push failed"
                    g["retries"] = g.get("retries", 0) + 1

        # Step 2: requeue retryable failed
        for g in gaps_list:
            if g.get("status") == "failed" and g.get("retries", 0) < MAX_RETRIES:
                log.info(f"REQUEUE {g['id']}: retry {g['retries']}")
                g["status"] = "pending"

        # Step 3: dispatch new pendings
        live_dispatched = sum(1 for g in gaps_list if g.get("status") == "dispatched")
        slots = max(0, MAX_PARALLEL - live_dispatched)
        if slots:
            picked = pick_pending_gaps(gaps_list, limit=slots)
            for g in picked:
                if dry_run:
                    log.info(f"  DRY-RUN dispatch {g['id']}: {g['title']}")
                else:
                    dispatch(g)

        # Refresh heartbeat before save (proves we're alive this cycle)
        if not dry_run:
            refresh_heartbeat(data)
        save_gaps(data)

        # Push lock + status update so other host sees fresh heartbeat
        if not dry_run:
            try:
                run(["git", "-C", str(REPO_ROOT), "add", "docs/audit_gaps.yaml"])
                # [skip ci] suppresses GitHub Actions for heartbeat commits.
                # paths-ignore in the workflows already handles audit_gaps.yaml-only
                # diffs, but [skip ci] is belt-and-suspenders + works on platforms
                # that don't support paths-ignore (e.g. some self-hosted runners).
                run(["git", "-C", str(REPO_ROOT), "commit", "-m",
                     f"chore(harness): heartbeat from {HOSTNAME} iter={iteration} [skip ci]",
                     "--allow-empty"], check=False)
                run(["git", "-C", str(REPO_ROOT), "push", "origin", "main"],
                    check=False, timeout=60)
            except Exception as e:
                log.warning(f"  heartbeat push skipped: {e}")

        # Stats
        counts = {"pending": 0, "dispatched": 0, "completed": 0, "failed": 0, "skipped": 0}
        for g in gaps_list:
            counts[g.get("status", "pending")] = counts.get(g.get("status", "pending"), 0) + 1
        log.info(f"Status: {counts}")

        # Termination check
        if counts["pending"] == 0 and counts["dispatched"] == 0:
            log.info("ALL GAPS PROCESSED — harness exiting.")
            return 0

        if once:
            return 0

        log.info(f"Sleeping {SLEEP_BETWEEN_LOOPS_S}s before next iteration ...")
        time.sleep(SLEEP_BETWEEN_LOOPS_S)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true", help="Run a single iteration")
    p.add_argument("--dry-run", action="store_true", help="No actual dispatch/commit, log plans")
    p.add_argument("--gap", help="Force-dispatch one specific gap id")
    args = p.parse_args(argv)

    if args.gap:
        data = load_gaps()
        for g in data["gaps"]:
            if g["id"] == args.gap:
                if dispatch(g):
                    save_gaps(data)
                    return 0
                return 1
        log.error(f"Unknown gap id: {args.gap}")
        return 2

    return harness_loop(once=args.once, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
