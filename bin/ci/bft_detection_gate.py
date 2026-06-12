"""BFT Adversarial Detection Gate.

Blocks PR merges if blue-team red-team detection rate drops below
MIN_DETECTION_RATE (default 73%) or any CRITICAL attack regresses
from caught to uncaught.

Pure stdlib. Exit 0 on pass, 1 on fail.
"""
import os
import re
import subprocess
import sys

CRITICAL_ATTACKS = {"B-03", "B-05", "D-04"}


def main() -> int:
    """Run BFT adversarial detection gate.

    Parses output from blue_team_score to verify detection rate meets
    MIN_DETECTION_RATE threshold (default 73%). Fails if any CRITICAL
    attack (B-03, B-05, D-04) regresses from caught to uncaught.

    Returns:
        0 if detection rate >= threshold and no critical regressions.
        1 if detection rate below threshold or critical attack uncaught.

    Exit codes:
        0: Gate pass
        1: Gate fail
    """
    threshold = int(os.environ.get("MIN_DETECTION_RATE", "73"))
    proc = subprocess.run(
        [sys.executable, "-m", "bin.red_team.blue_team_score"],
        capture_output=True,
        text=True,
    )
    output = proc.stdout + proc.stderr
    sys.stdout.write(output)

    m = re.search(r"OVERALL DETECTION RATE:\s*(\d+)/(\d+)\s*\((\d+)%\)", output)
    if not m:
        sys.stderr.write(
            "BFT GATE FAIL: could not parse 'OVERALL DETECTION RATE' from scorer.\n"
        )
        return 1
    caught, total, pct = int(m.group(1)), int(m.group(2)), int(m.group(3))

    # Regression: any CRITICAL attack uncaught
    row_re = re.compile(
        r"^(B-\d+|C-\d+|D-\d+)\s+\S+\s+(\S+)\s+(\S+\s*\S*)\s",
        re.MULTILINE,
    )
    uncaught_critical = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] in CRITICAL_ATTACKS:
            sev = parts[2]
            caught_marker = parts[3]
            if sev == "critical" and caught_marker != "✅":
                uncaught_critical.append(parts[0])

    if uncaught_critical:
        sys.stderr.write(
            f"BFT GATE FAIL: CRITICAL attack(s) regressed (caught -> uncaught): "
            f"{', '.join(uncaught_critical)}\n"
        )
        return 1

    if pct < threshold:
        sys.stderr.write(
            f"BFT GATE FAIL: detection rate {pct}% ({caught}/{total}) "
            f"below threshold {threshold}%.\n"
            f"Baseline: recorder-v0.24.0-wave3-73pct\n"
        )
        return 1

    sys.stdout.write(
        f"\nBFT GATE PASS: {pct}% ({caught}/{total}) >= {threshold}% threshold.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
