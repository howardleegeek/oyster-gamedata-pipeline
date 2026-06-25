#!/usr/bin/env python3
"""
Preflight integration test.

Runs bin/preflight_recorder.py on the session, parses preflight_report.json:
- Assert all 10 PRD checks present
- Assert real display resolution + DPI detected (not all-zero)
- Assert audio device enumerated
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict


def run_preflight(session_dir: str) -> Dict[str, Any]:
    """Run preflight_recorder.py and return parsed results."""
    preflight_script = Path(__file__).parent.parent / "preflight_recorder.py"

    if not preflight_script.exists():
        return {"status": "SKIP", "evidence": "preflight_recorder.py not found"}

    # Run preflight
    try:
        result = subprocess.run(
            [sys.executable, str(preflight_script)],
            cwd=session_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )

        # Look for preflight_report.json in session dir
        report_path = Path(session_dir) / "preflight_report.json"
        if not report_path.exists():
            return {
                "status": "FAIL",
                "evidence": f"preflight_report.json not found. stderr: {result.stderr[:200]}",
            }

        with open(report_path) as f:
            report = json.load(f)

        return {"status": "PASS", "report": report}
    except subprocess.TimeoutExpired:
        return {"status": "FAIL", "evidence": "preflight timeout"}
    except Exception as e:
        return {"status": "FAIL", "evidence": str(e)}


def validate_preflight_report(report: Dict[str, Any]) -> Dict[str, Any]:
    """Validate preflight report has all required checks."""
    required_checks = [
        "display_resolution",
        "display_dpi",
        "gpu_driver",
        "gpu_vram",
        "audio_device",
        "audio_sample_rate",
        "network_connectivity",
        "disk_space",
        "memory_available",
        "os_version",
    ]

    # Check all 10 PRD checks present
    if "checks" not in report:
        return {"status": "FAIL", "evidence": "no 'checks' key in report"}

    checks = report.get("checks", {})
    missing = [c for c in required_checks if c not in checks]
    if missing:
        return {"status": "FAIL", "evidence": f"missing checks: {missing}"}

    # Check display resolution is real (not all-zero)
    display_res = checks.get("display_resolution", {})
    if isinstance(display_res, dict):
        width = display_res.get("width", 0)
        height = display_res.get("height", 0)
        if width == 0 or height == 0:
            return {"status": "FAIL", "evidence": "display resolution is zero"}

    # Check DPI is real (not all-zero)
    display_dpi = checks.get("display_dpi", {})
    if isinstance(display_dpi, dict):
        dpi = display_dpi.get("dpi", 0)
        if dpi == 0:
            return {"status": "FAIL", "evidence": "display DPI is zero"}

    # Check audio device enumerated
    audio = checks.get("audio_device", {})
    if isinstance(audio, dict):
        device = audio.get("device", "")
        if not device:
            return {"status": "FAIL", "evidence": "no audio device enumerated"}

    return {"status": "PASS", "evidence": "all 10 checks present, display/audio valid"}


def main():
    parser = argparse.ArgumentParser(description="Preflight integration test")
    parser.add_argument("--session-dir", required=True, help="Session directory")
    args = parser.parse_args()

    # Run preflight
    result = run_preflight(args.session_dir)

    if result["status"] == "SKIP":
        print(f"SKIP: {result.get('evidence', 'skipped')}")
        sys.exit(0)

    if result["status"] == "FAIL":
        print(f"FAIL: {result.get('evidence', 'failed')}")
        sys.exit(1)

    # Validate report
    validation = validate_preflight_report(result.get("report", {}))

    if validation["status"] == "PASS":
        print(f"PASS: {validation['evidence']}")
        sys.exit(0)
    else:
        print(f"FAIL: {validation['evidence']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
