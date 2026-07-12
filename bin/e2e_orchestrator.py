#!/usr/bin/env python3
"""
E2E Test Orchestrator for minipc1 session testing.

This orchestrator:
1. SSHs into minipc1 (Tailscale 100.105.39.60)
2. Pulls latest finalized recorder session via tar-over-SSH
3. Runs canonical_pipeline.py --target-score 101
4. Runs per-feature integration tests for 6 cluster deliverables
5. Emits e2e_test_report.json with per-feature PASS/FAIL
6. Sends notification on FAIL
7. Archives audit artifacts to ~/Downloads/e2e_results/<session_id>/
"""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

# Constants
MINIPC1_HOST = "100.105.39.60"
MINIPC1_USER = "Administrator"
DEFAULT_ARCHIVE_DIR = os.path.expanduser("~/Downloads/e2e_results")
DEFAULT_SESSION_DIR = "/tmp/e2e_session_{uuid}"
RECORDINGS_PATH = r"%LOCALAPPDATA%\GameData Recorder\recordings\*"

# Feature test modules
FEATURE_TESTS = [
    "test_preflight_integration",
    "test_watchdog_integration",
    "test_provenance_integration",
    "test_zbuffer_integration",
    "test_batch_integration",
    "test_skip_depth_baseline",
]


def run_ssh_cmd(host: str, user: str, cmd: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a command via SSH."""
    ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", f"{user}@{host}", cmd]
    return subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=timeout)


def discover_latest_session(host: str, user: str) -> Optional[str]:
    """Discover the latest finalized session on minipc1."""
    # PowerShell command to find latest session directory
    ps_cmd = (
        r"Get-ChildItem -Path $env:LOCALAPPDATA\\GameData\\Recorder\\recordings\\* "
        r"| Where-Object { $_.PSIsContainer } "
        r"| Sort-Object LastWriteTime -Descending "
        r"| Select-Object -First 1 -ExpandProperty Name"
    )
    result = run_ssh_cmd(host, user, f'powershell -Command "{ps_cmd}"')
    if result.returncode != 0:
        print(f"Failed to discover session: {result.stderr}")
        return None
    session_id = result.stdout.strip()
    if not session_id:
        return None
    print(f"Discovered latest session: {session_id}")
    return session_id


def pull_session_via_tar(host: str, user: str, session_id: str, local_stage_dir: str) -> bool:
    """Pull session from minipc1 using tar-over-SSH (handles space-in-path)."""
    remote_recordings_path = rf"%LOCALAPPDATA%\GameData Recorder\recordings\{session_id}"
    
    # Use tar -c | tar -x pattern for reliable transfer
    tar_cmd = (
        f'cd "{remote_recordings_path}" && tar -c -f - .'
    )
    
    ssh_tar_cmd = f'ssh -o StrictHostKeyChecking=no {user}@{host} "{tar_cmd}"'
    
    # Create local stage directory
    os.makedirs(local_stage_dir, exist_ok=True)
    
    # Pull via tar
    try:
        proc = subprocess.Popen(
            ssh_tar_cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Extract locally (ignore return code - tar will error via proc if extraction fails)
        subprocess.run(
            ["tar", "-x", "-f", "-", "-C", local_stage_dir],
            stdin=proc.stdout,
            stderr=subprocess.PIPE
        )
        
        proc.wait()
        
        if proc.returncode != 0:
            stderr = proc.stderr.read().decode() if proc.stderr else ""
            print(f"Failed to pull session: {stderr}")
            return False
            
        print(f"Successfully pulled session to {local_stage_dir}")
        return True
    except Exception as e:
        print(f"Exception pulling session: {e}")
        return False


def run_canonical_pipeline(session_dir: str, target_score: int) -> Dict[str, Any]:
    """Run canonical_pipeline.py on the session."""
    pipeline_script = Path(__file__).parent / "canonical_pipeline.py"
    
    if not pipeline_script.exists():
        return {"status": "FAIL", "score": "0/0", "error": "canonical_pipeline.py not found"}
    
    # Bug-fix 2026-05-17: canonical_pipeline.py takes session_dir as positional
    # arg (per local repo signature). Previously this only set cwd but didn't
    # pass session_dir, causing argparse to fail "the following arguments are required".
    cmd = [sys.executable, str(pipeline_script), str(session_dir),
           "--target-score", str(target_score)]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600
        )
        
        # Bug-fix 2026-05-17: the previous regex `(\d+)/(\d+)` matched the FIRST
        # N/M anywhere in output — canonical_pipeline's step counter is
        # "[1/10] Transform game_state.jsonl", which the greedy regex grabbed
        # INSTEAD of the actual audit score. Anchor the regex to canonical's
        # explicit "OK: <pass>/<total> PASS" line at end of step 10.
        output_blob = result.stdout + result.stderr
        score_match = re.search(r"OK:\s*(\d+)/(\d+)\s*PASS", output_blob)
        if not score_match:
            # Fallback A: AUDIT: PASS=<n> FAIL=<n> SKIP=<n> TOTAL=<n>
            audit_pass = re.search(r"AUDIT:\s*PASS=(\d+).+?TOTAL=(\d+)", output_blob)
            if audit_pass:
                score = f"{audit_pass.group(1)}/{audit_pass.group(2)}"
            else:
                score = "unknown"
        else:
            score = f"{score_match.group(1)}/{score_match.group(2)}"
        
        status = "PASS" if result.returncode == 0 else "FAIL"
        
        return {
            "status": status,
            "score": score,
            "output": result.stdout + result.stderr
        }
    except subprocess.TimeoutExpired:
        return {"status": "FAIL", "score": "0/0", "error": "timeout"}
    except Exception as e:
        return {"status": "FAIL", "score": "0/0", "error": str(e)}


def run_feature_test(test_name: str, session_dir: str) -> Dict[str, Any]:
    """Run a feature integration test."""
    test_script = Path(__file__).parent / "e2e_tests" / f"{test_name}.py"
    
    if not test_script.exists():
        return {"status": "SKIP", "evidence": f"test script {test_name}.py not found"}
    
    cmd = [sys.executable, str(test_script), "--session-dir", session_dir]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300
        )
        
        # Parse output for status
        output = result.stdout + result.stderr
        
        # Look for status markers in output
        if "PASS" in output and "FAIL" not in output:
            status = "PASS"
            evidence = "test passed"
        elif "SKIP" in output:
            status = "SKIP"
            # Extract skip reason
            skip_match = re.search(r"SKIP:\s*(.+?)(?:\n|$)", output)
            evidence = skip_match.group(1) if skip_match else "skipped"
        else:
            status = "FAIL"
            evidence = output[:500]  # First 500 chars of error
        
        return {"status": status, "evidence": evidence}
    except subprocess.TimeoutExpired:
        return {"status": "FAIL", "evidence": "timeout"}
    except Exception as e:
        return {"status": "FAIL", "evidence": str(e)}


def check_idempotent(session_id: str, archive_dir: str) -> bool:
    """Check if session already tested (idempotent)."""
    session_archive = Path(archive_dir) / session_id
    report_file = session_archive / "e2e_test_report.json"
    return report_file.exists()


def archive_artifacts(session_id: str, session_dir: str, archive_dir: str, report: Dict) -> List[str]:
    """Archive test artifacts."""
    session_archive = Path(archive_dir) / session_id
    session_archive.mkdir(parents=True, exist_ok=True)
    
    artifacts = []
    
    # Copy session directory
    dest_session = session_archive / "session"
    if Path(session_dir).exists():
        shutil.copytree(session_dir, dest_session, dirs_exist_ok=True)
        artifacts.append(str(dest_session))
    
    # Write report
    report_file = session_archive / "e2e_test_report.json"
    with open(report_file, "w") as f:
        json.dump(report, f, indent=2)
    artifacts.append(str(report_file))
    
    return artifacts


def send_notification(notify_backend: str, session_id: str, report: Dict) -> bool:
    """Send notification on test result."""
    if notify_backend == "log":
        return send_log_notification(session_id, report)
    elif notify_backend == "telegram":
        return send_telegram_notification(session_id, report)
    elif notify_backend == "slack":
        return send_slack_notification(session_id, report)
    elif notify_backend == "pushnotification":
        return send_push_notification(session_id, report)
    else:
        print(f"Unknown notification backend: {notify_backend}")
        return False


def send_log_notification(session_id: str, report: Dict) -> bool:
    """Log notification to file."""
    log_file = Path(DEFAULT_ARCHIVE_DIR) / "notifications.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    
    with open(log_file, "a") as f:
        f.write(f"[{datetime.datetime.utcnow().isoformat()}] ")
        f.write(f"Session: {session_id}, Overall: {report.get('overall', 'UNKNOWN')}\n")
    
    print(f"Logged notification to {log_file}")
    return True


def send_telegram_notification(session_id: str, report: Dict) -> bool:
    """Send notification via Telegram."""
    # Import notification module
    try:
        from e2e_notify import TelegramNotifier
        notifier = TelegramNotifier()
        return notifier.send(session_id, report)
    except ImportError:
        print("e2e_notify module not available, falling back to log")
        return send_log_notification(session_id, report)
    except Exception as e:
        print(f"Telegram notification failed: {e}")
        return send_log_notification(session_id, report)


def send_slack_notification(session_id: str, report: Dict) -> bool:
    """Send notification via Slack."""
    try:
        from e2e_notify import SlackNotifier
        notifier = SlackNotifier()
        return notifier.send(session_id, report)
    except ImportError:
        print("e2e_notify module not available, falling back to log")
        return send_log_notification(session_id, report)
    except Exception as e:
        print(f"Slack notification failed: {e}")
        return send_log_notification(session_id, report)


def send_push_notification(session_id: str, report: Dict) -> bool:
    """Send push notification via Claude Code PushNotification tool."""
    overall = report.get("overall", "UNKNOWN")
    message = f"E2E Test {overall}: Session {session_id}"
    
    try:
        # Try to use Claude Code's PushNotification tool
        import subprocess
        result = subprocess.run(
            ["osascript", "-e", f'display notification "{message}" with title "E2E Test"'],
            capture_output=True
        )
        return result.returncode == 0
    except Exception as e:
        print(f"Push notification failed: {e}")
        return send_log_notification(session_id, report)


def main():
    parser = argparse.ArgumentParser(description="E2E Test Orchestrator for minipc1")
    parser.add_argument(
        "--session-source",
        choices=["minipc1", "local"],
        default="minipc1",
        help="Source of session (minipc1 or local)"
    )
    parser.add_argument(
        "--session-dir",
        type=str,
        help="Local session directory (for --session-source local)"
    )
    parser.add_argument(
        "--target-score",
        type=int,
        default=101,
        help="Target score for canonical_pipeline"
    )
    parser.add_argument(
        "--notify-on-fail",
        type=str,
        choices=["telegram", "slack", "log", "pushnotification"],
        default="log",
        help="Notification backend on failure"
    )
    parser.add_argument(
        "--archive-dir",
        type=str,
        default=DEFAULT_ARCHIVE_DIR,
        help="Archive directory for results"
    )
    parser.add_argument(
        "--skip-idempotency",
        action="store_true",
        help="Skip idempotency check"
    )
    
    args = parser.parse_args()
    
    # Determine session
    if args.session_source == "minipc1":
        # Discover latest session
        session_id = discover_latest_session(MINIPC1_HOST, MINIPC1_USER)
        if not session_id:
            print("ERROR: Could not discover latest session on minipc1")
            sys.exit(1)
        
        # Check idempotency
        if not args.skip_idempotency and check_idempotent(session_id, args.archive_dir):
            print(f"Session {session_id} already tested (idempotent), skipping")
            sys.exit(0)
        
        # Pull session
        stage_dir = DEFAULT_SESSION_DIR.format(uuid=uuid.uuid4().hex[:8])
        if not pull_session_via_tar(MINIPC1_HOST, MINIPC1_USER, session_id, stage_dir):
            print("ERROR: Failed to pull session from minipc1")
            sys.exit(1)
        
        session_dir = stage_dir
    else:
        # Local session
        if not args.session_dir:
            print("ERROR: --session-dir required for --session-source local")
            sys.exit(1)
        
        session_dir = args.session_dir
        # Extract session_id from directory name
        session_id = Path(session_dir).name
    
    # Run canonical pipeline
    print(f"Running canonical_pipeline on {session_id}...")
    pipeline_result = run_canonical_pipeline(session_dir, args.target_score)
    print(f"Canonical pipeline: {pipeline_result['status']} ({pipeline_result.get('score', 'N/A')})")
    
    # Run feature tests
    features = {}
    for test_name in FEATURE_TESTS:
        print(f"Running {test_name}...")
        result = run_feature_test(test_name, session_dir)
        features[test_name.replace("test_", "").replace("_integration", "")] = result
        print(f"  {test_name}: {result['status']} - {result.get('evidence', '')}")
    
    # Determine overall status
    all_pass = pipeline_result["status"] == "PASS"
    for feat in features.values():
        if feat["status"] not in ("PASS", "SKIP"):
            all_pass = False
            break
    
    overall = "PASS" if all_pass else "FAIL"
    
    # Build report
    report = {
        "ran_at": datetime.datetime.utcnow().isoformat() + "Z",
        "session_id": session_id,
        "session_source": args.session_source,
        "canonical_pipeline": {
            "status": pipeline_result["status"],
            "score": pipeline_result.get("score", "unknown")
        },
        "features": features,
        "overall": overall,
        "notifications_sent": [],
        "artifacts": []
    }
    
    # Archive artifacts
    artifacts = archive_artifacts(session_id, session_dir, args.archive_dir, report)
    report["artifacts"] = artifacts
    
    # Send notification on failure
    notifications_sent = []
    if overall == "FAIL" and send_notification(args.notify_on_fail, session_id, report):
        notifications_sent.append(args.notify_on_fail)
    
    report["notifications_sent"] = notifications_sent
    
    # Write final report
    report_file = Path(args.archive_dir) / session_id / "e2e_test_report.json"
    with open(report_file, "w") as f:
        json.dump(report, f, indent=2)
    
    print(f"\nE2E Test Report: {overall}")
    print(f"Report saved to: {report_file}")
    
    # Cleanup on success
    if overall == "PASS" and args.session_source == "minipc1":
        shutil.rmtree(session_dir, ignore_errors=True)
        print(f"Cleaned up {session_dir}")
    
    # Exit with appropriate code
    sys.exit(0 if overall == "PASS" else 1)


if __name__ == "__main__":
    main()
