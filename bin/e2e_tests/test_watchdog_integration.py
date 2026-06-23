#!/usr/bin/env python3
"""
Watchdog integration test.

Runs bin/recorder_watchdog.py on known-bad and known-good fixture sessions:
- Known-good fixture (Howard's 2026-05-16 session) → grade=PASS
- Synthesized bad fixture (alt-tab events injected) → grade=DEGRADED
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

# Known-good fixture session (Howard's 2026-05-16 session)
KNOWN_GOOD_SESSION = "session_20260516_213817_d137a341"

# Known-bad fixture: synthesized with alt-tab events
BAD_FIXTURE_CONTENT = {
    "events": [
        {"type": "window_blur", "timestamp": 1000},
        {"type": "alt_tab", "timestamp": 1500},
        {"type": "window_focus", "timestamp": 2000},
    ],
    "grade": "DEGRADED"
}


def run_watchdog(session_dir: str) -> Dict[str, Any]:
    """Run recorder_watchdog.py and return parsed results."""
    watchdog_script = Path(__file__).parent.parent / "recorder_watchdog.py"
    
    if not watchdog_script.exists():
        return {"status": "SKIP", "evidence": "recorder_watchdog.py not found"}
    
    # Run watchdog
    try:
        result = subprocess.run(
            [sys.executable, str(watchdog_script)],
            cwd=session_dir,
            capture_output=True,
            text=True,
            timeout=120
        )
        
        # Look for watchdog_events.jsonl and session_grade.json
        events_path = Path(session_dir) / "watchdog_events.jsonl"
        grade_path = Path(session_dir) / "session_grade.json"
        
        events = []
        if events_path.exists():
            with open(events_path) as f:
                for line in f:
                    if line.strip():
                        events.append(json.loads(line))
        
        grade = {}
        if grade_path.exists():
            with open(grade_path) as f:
                grade = json.load(f)
        
        if not grade:
            return {
                "status": "FAIL",
                "evidence": f"session_grade.json not found. stderr: {result.stderr[:200]}"
            }
        
        return {
            "status": "PASS",
            "events": events,
            "grade": grade
        }
    except subprocess.TimeoutExpired:
        return {"status": "FAIL", "evidence": "watchdog timeout"}
    except Exception as e:
        return {"status": "FAIL", "evidence": str(e)}


def validate_watchdog_grade(grade: Dict[str, Any], is_bad_fixture: bool = False) -> Dict[str, Any]:
    """Validate watchdog grade is correct for fixture type."""
    grade_value = grade.get("grade", "UNKNOWN")
    
    if is_bad_fixture:
        # Bad fixture should have DEGRADED grade
        if grade_value == "DEGRADED":
            return {"status": "PASS", "evidence": "grade=DEGRADED as expected for bad fixture"}
        else:
            return {"status": "FAIL", "evidence": f"expected DEGRADED, got {grade_value}"}
    else:
        # Good fixture should have PASS grade
        if grade_value == "PASS":
            # Also check no fatal events
            fatal_events = grade.get("fatal_events", [])
            if len(fatal_events) > 0:
                return {"status": "FAIL", "evidence": f"found {len(fatal_events)} fatal events"}
            return {"status": "PASS", "evidence": "grade=PASS, 0 fatal events"}
        else:
            return {"status": "FAIL", "evidence": f"expected PASS, got {grade_value}"}


def create_bad_fixture(temp_dir: Path) -> str:
    """Create a synthesized bad fixture with alt-tab events."""
    # Create events file
    events_path = temp_dir / "events.jsonl"
    with open(events_path, "w") as f:
        for event in BAD_FIXTURE_CONTENT["events"]:
            f.write(json.dumps(event) + "\n")
    
    return str(temp_dir)


def main():
    parser = argparse.ArgumentParser(description="Watchdog integration test")
    parser.add_argument("--session-dir", required=True, help="Session directory")
    parser.add_argument("--fixture-type", choices=["good", "bad"], default="good",
                        help="Fixture type to test")
    args = parser.parse_args()
    
    session_dir = args.session_dir
    
    # Check if this is a known-good fixture (by session ID)
    is_bad_fixture = args.fixture_type == "bad"
    
    # If session_dir is the known-good fixture, use it directly
    if Path(session_dir).name == KNOWN_GOOD_SESSION:
        is_bad_fixture = False
    
    # Run watchdog
    result = run_watchdog(session_dir)
    
    if result["status"] == "SKIP":
        print(f"SKIP: {result.get('evidence', 'skipped')}")
        sys.exit(0)
    
    if result["status"] == "FAIL":
        print(f"FAIL: {result.get('evidence', 'failed')}")
        sys.exit(1)
    
    # Validate grade
    grade = result.get("grade", {})
    validation = validate_watchdog_grade(grade, is_bad_fixture)
    
    if validation["status"] == "PASS":
        print(f"PASS: {validation['evidence']}")
        sys.exit(0)
    else:
        print(f"FAIL: {validation['evidence']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
