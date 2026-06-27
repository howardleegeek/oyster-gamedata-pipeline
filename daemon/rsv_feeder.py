#!/usr/bin/env python3
"""
RSV FEEDER DAEMON — S21
========================

Scans ~/Documents/OysterClips/finalized/ for session directories,
runs real_session_validator.py on each new session, tracks state
to avoid re-processing, and aggregates verdicts into
dashboard/buyer_ready_pct.json.

Usage:
    python3 daemon/rsv_feeder.py --once          # single pass
    python3 daemon/rsv_feeder.py --once --dry-run # scan only, no RSV calls
    python3 daemon/rsv_feeder.py                  # daemon loop (every 6h)

Pure Python stdlib only.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import pathlib
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
RSV_SCRIPT = REPO_ROOT / "bin" / "real_session_validator.py"
DEFAULT_FINALIZED_DIR = pathlib.Path("~/Documents/OysterClips/finalized").expanduser()
DEFAULT_STATE_FILE = pathlib.Path("~/.oyster/rsv_feeder_state.json").expanduser()
DEFAULT_DASHBOARD_FILE = REPO_ROOT / "dashboard" / "buyer_ready_pct.json"

POLL_INTERVAL_SECONDS = 6 * 60 * 60  # 6 hours

REQUIRED_FILES = {"recording.mp4", "game_state.jsonl"}


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------


def load_state(state_path: pathlib.Path) -> dict:
    """Load existing state file. Returns {session_id: {sha256, verdict, processed_at}}."""
    if not state_path.exists():
        return {}
    try:
        with open(state_path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state_path: pathlib.Path, state: dict) -> None:
    """Persist state to disk."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with open(state_path, "w") as f:
        json.dump(state, f, indent=2)


def compute_session_hash(session_dir: pathlib.Path) -> str:
    """Compute sha256 of session directory contents (sorted file list + sizes)."""
    h = hashlib.sha256()
    for entry in sorted(session_dir.iterdir()):
        if entry.is_file():
            h.update(entry.name.encode())
            h.update(str(entry.stat().st_size).encode())
        elif entry.is_dir():
            h.update(entry.name.encode())
            h.update(b"DIR")
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------


def discover_sessions(root: pathlib.Path) -> list[pathlib.Path]:
    """Walk one level deep under root, return dirs that contain required files."""
    sessions: list[pathlib.Path] = []
    if not root.is_dir():
        return sessions
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            present = {f.name for f in entry.iterdir()}
            if REQUIRED_FILES.issubset(present):
                sessions.append(entry)
    return sessions


def filter_new_sessions(sessions: list[pathlib.Path], state: dict) -> list[pathlib.Path]:
    """Return sessions not yet in state or whose hash changed."""
    new: list[pathlib.Path] = []
    for s in sessions:
        sid = s.name
        current_hash = compute_session_hash(s)
        if sid not in state:
            new.append(s)
        elif state[sid].get("sha256") != current_hash:
            # Content changed — re-process
            new.append(s)
    return new


# ---------------------------------------------------------------------------
# RSV invocation
# ---------------------------------------------------------------------------


def run_rsv(session_dir: pathlib.Path, output_path: pathlib.Path) -> dict:
    """
    Run real_session_validator.py --sample 1 --output <path> for a single session.
    Returns the parsed JSON verdict dict, or error dict on failure.
    """
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(RSV_SCRIPT),
                "--sessions-root",
                str(session_dir.parent),
                "--sample",
                "1",
                "--output",
                str(output_path),
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=900,  # 15 min max per session
        )
        if output_path.exists():
            with open(output_path, "r") as f:
                return json.load(f)
        # Fallback: try parsing stdout
        try:
            return json.loads(result.stdout.strip())
        except (json.JSONDecodeError, ValueError):
            return {
                "error": "no output",
                "returncode": result.returncode,
                "stderr": result.stderr.strip(),
            }
    except subprocess.TimeoutExpired:
        return {"error": "TIMEOUT"}
    except Exception as exc:
        return {"error": str(exc)}


def extract_verdict(rsv_output: dict) -> str:
    """
    Extract the overall verdict from RSV output.
    Returns 'BUYER_READY' for PASS, 'NOT_READY' otherwise.
    """
    # RSV output may have different shapes
    if "error" in rsv_output:
        return "NOT_READY"

    # Check summary
    summary = rsv_output.get("summary", {})
    pass_count = summary.get("PASS", 0)
    total = summary.get("total", 0)

    if total > 0 and pass_count == total:
        return "BUYER_READY"

    # Check sessions list
    sessions = rsv_output.get("sessions", [])
    if sessions:
        overall = sessions[0].get("overall", "UNKNOWN")
        if overall == "PASS":
            return "BUYER_READY"
        return "NOT_READY"

    # Check top-level verdict
    verdict = rsv_output.get("verdict", rsv_output.get("overall", "UNKNOWN"))
    if verdict == "PASS":
        return "BUYER_READY"
    return "NOT_READY"


# ---------------------------------------------------------------------------
# Dashboard aggregation
# ---------------------------------------------------------------------------


def load_dashboard(dashboard_path: pathlib.Path) -> dict:
    """Load existing dashboard JSON."""
    if not dashboard_path.exists():
        return {"total": 0, "buyer_ready": 0, "pct": 0.0, "updated_at": ""}
    try:
        with open(dashboard_path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"total": 0, "buyer_ready": 0, "pct": 0.0, "updated_at": ""}


def update_dashboard(dashboard_path: pathlib.Path, verdicts: list[str]) -> dict:
    """
    Accumulate verdicts into dashboard JSON.
    Returns the updated dashboard dict.
    """
    dashboard = load_dashboard(dashboard_path)
    for v in verdicts:
        dashboard["total"] = dashboard.get("total", 0) + 1
        if v == "BUYER_READY":
            dashboard["buyer_ready"] = dashboard.get("buyer_ready", 0) + 1

    total = dashboard["total"]
    buyer_ready = dashboard["buyer_ready"]
    dashboard["pct"] = round(buyer_ready / total, 4) if total > 0 else 0.0
    dashboard["updated_at"] = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    dashboard_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dashboard_path, "w") as f:
        json.dump(dashboard, f, indent=2)

    return dashboard


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run_once(
    finalized_dir: pathlib.Path,
    state_path: pathlib.Path,
    dashboard_path: pathlib.Path,
    dry_run: bool = False,
) -> int:
    """
    Single pass: discover new sessions, run RSV, update state + dashboard.
    Returns number of sessions processed.
    """
    state = load_state(state_path)
    sessions = discover_sessions(finalized_dir)
    new_sessions = filter_new_sessions(sessions, state)

    if not new_sessions:
        print(f"[rsv_feeder] No new sessions in {finalized_dir}")
        return 0

    print(f"[rsv_feeder] Found {len(new_sessions)} new session(s) to process")

    verdicts: list[str] = []

    for session_dir in new_sessions:
        sid = session_dir.name
        session_hash = compute_session_hash(session_dir)

        if dry_run:
            print(f"[rsv_feeder] [DRY-RUN] Would process: {sid}")
            verdicts.append("BUYER_READY")  # optimistic for dry-run
            state[sid] = {
                "sha256": session_hash,
                "verdict": "BUYER_READY (dry-run)",
                "processed_at": datetime.datetime.now(datetime.timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
            }
            continue

        # Run RSV
        output_path = pathlib.Path(f"/tmp/rsv_{sid}.json")
        print(f"[rsv_feeder] Running RSV on {sid} ...")
        rsv_output = run_rsv(session_dir, output_path)
        verdict = extract_verdict(rsv_output)
        verdicts.append(verdict)

        state[sid] = {
            "sha256": session_hash,
            "verdict": verdict,
            "processed_at": datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        }
        print(f"[rsv_feeder] {sid} → {verdict}")

    # Save state
    save_state(state_path, state)
    print(f"[rsv_feeder] State saved to {state_path}")

    # Update dashboard
    if verdicts:
        dashboard = update_dashboard(dashboard_path, verdicts)
        print(
            f"[rsv_feeder] Dashboard: {dashboard['buyer_ready']}/{dashboard['total']} "
            f"buyer_ready ({dashboard['pct'] * 100:.1f}%)"
        )

    return len(new_sessions)


def run_daemon(
    finalized_dir: pathlib.Path,
    state_path: pathlib.Path,
    dashboard_path: pathlib.Path,
    dry_run: bool = False,
) -> None:
    """Daemon loop: run once every POLL_INTERVAL_SECONDS."""
    print(f"[rsv_feeder] Starting daemon (poll every {POLL_INTERVAL_SECONDS}s)")
    while True:
        try:
            processed = run_once(finalized_dir, state_path, dashboard_path, dry_run)
            if processed > 0:
                print(f"[rsv_feeder] Processed {processed} session(s)")
        except Exception as exc:
            print(f"[rsv_feeder] Error: {exc}", file=sys.stderr)
        time.sleep(POLL_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="RSV Feeder Daemon — scans finalized sessions and aggregates verdicts"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single pass and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan only, do not invoke RSV or write outputs",
    )
    parser.add_argument(
        "--finalized-dir",
        type=str,
        default=None,
        help="Override finalized sessions directory",
    )
    parser.add_argument(
        "--state-file",
        type=str,
        default=None,
        help="Override state file path",
    )
    parser.add_argument(
        "--dashboard-file",
        type=str,
        default=None,
        help="Override dashboard output path",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    finalized_dir = (
        pathlib.Path(args.finalized_dir).expanduser()
        if args.finalized_dir
        else DEFAULT_FINALIZED_DIR
    )
    state_path = (
        pathlib.Path(args.state_file).expanduser() if args.state_file else DEFAULT_STATE_FILE
    )
    dashboard_path = (
        pathlib.Path(args.dashboard_file) if args.dashboard_file else DEFAULT_DASHBOARD_FILE
    )

    if args.once:
        run_once(finalized_dir, state_path, dashboard_path, args.dry_run)
        # exit 0 whether we processed sessions or not (no new sessions is not an error)
        return 0
    else:
        run_daemon(finalized_dir, state_path, dashboard_path, args.dry_run)
        return 0


if __name__ == "__main__":
    sys.exit(main())
