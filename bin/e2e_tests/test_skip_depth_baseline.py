#!/usr/bin/env python3
"""
Skip-depth baseline integration test.

Runs canonical_pipeline with --skip-depth against the test session,
asserts audit returns PASS >= 89 (contributor mode baseline).
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

# Minimum baseline score for contributor mode
MIN_BASELINE_SCORE = 89


def run_canonical_pipeline_skip_depth(session_dir: str) -> Dict[str, Any]:
    """Run canonical_pipeline.py with --skip-depth flag."""
    pipeline_script = Path(__file__).parent.parent / "canonical_pipeline.py"

    if not pipeline_script.exists():
        return {"status": "SKIP", "evidence": "canonical_pipeline.py not found"}

    # Run with --skip-depth
    target_score = str(MIN_BASELINE_SCORE)
    try:
        result = subprocess.run(
            [sys.executable, str(pipeline_script), "--skip-depth", "--target-score", target_score],
            cwd=session_dir,
            capture_output=True,
            text=True,
            timeout=600
        )

        output = result.stdout + result.stderr

        # Parse score from output
        score_match = re.search(r"(\d+)/(\d+)", output)
        if score_match:
            score = int(score_match.group(1))
            max_score = int(score_match.group(2))
        else:
            score = 0
            max_score = 0

        status = "PASS" if result.returncode == 0 else "FAIL"

        return {
            "status": status,
            "score": score,
            "max_score": max_score,
            "output": output
        }
    except subprocess.TimeoutExpired:
        return {"status": "FAIL", "evidence": "timeout"}
    except Exception as e:
        return {"status": "FAIL", "evidence": str(e)}


def validate_baseline_score(result: Dict[str, Any]) -> Dict[str, Any]:
    """Validate that baseline score >= 89."""
    score = result.get("score", 0)

    if score >= MIN_BASELINE_SCORE:
        return {
            "status": "PASS",
            "evidence": f"baseline {score} PASS confirmed (>= {MIN_BASELINE_SCORE})"
        }
    else:
        return {
            "status": "FAIL",
            "evidence": f"baseline {score} below minimum {MIN_BASELINE_SCORE}"
        }


def main():
    parser = argparse.ArgumentParser(description="Skip-depth baseline integration test")
    parser.add_argument("--session-dir", required=True, help="Session directory")
    parser.add_argument("--min-score", type=int, default=MIN_BASELINE_SCORE,
                        help=f"Minimum baseline score (default: {MIN_BASELINE_SCORE})")
    args = parser.parse_args()

    # Run canonical pipeline with --skip-depth
    result = run_canonical_pipeline_skip_depth(args.session_dir)

    if result["status"] == "SKIP":
        print(f"SKIP: {result.get('evidence', 'skipped')}")
        sys.exit(0)

    if result["status"] == "FAIL":
        print(f"FAIL: {result.get('evidence', 'failed')}")
        sys.exit(1)

    # Validate baseline score
    validation = validate_baseline_score(result)

    if validation["status"] == "PASS":
        print(f"PASS: {validation['evidence']}")
        sys.exit(0)
    else:
        print(f"FAIL: {validation['evidence']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
