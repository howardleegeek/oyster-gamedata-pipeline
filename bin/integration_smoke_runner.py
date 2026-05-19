#!/usr/bin/env python3
"""
integration_smoke_runner.py — orchestrates e2e smoke test + semantic validator.

Usage:
    python integration_smoke_runner.py --placeholders <dir> --output <dir>
"""

import argparse
import subprocess
import sys
import os


def run_cmd(cmd, cwd=None):
    """Execute shell command, return (success, stdout, stderr)."""
    try:
        result = subprocess.run(cmd, shell=True, cwd=cwd,
                                capture_output=True, text=True, timeout=300)
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "Command timed out"
    except Exception as e:
        return False, "", str(e)


def main():
    parser = argparse.ArgumentParser(description="Run e2e smoke + semantic validator")
    parser.add_argument("--placeholders", required=True, help="Path to placeholders dir")
    parser.add_argument("--output", required=True, help="Path to output dir")
    args = parser.parse_args()

    placeholders = os.path.abspath(args.placeholders)
    output = os.path.abspath(args.output)

    print(f"=== Running e2e_smoke.sh ===")
    print(f"Placeholders: {placeholders}, Output: {output}")

    # Step 1: Run e2e_smoke.sh
    e2e_script = os.path.join(placeholders, "e2e_smoke.sh")
    if not os.path.exists(e2e_script):
        print(f"ERROR: e2e_smoke.sh not found at {e2e_script}")
        print("FAIL")
        sys.exit(1)

    ok, out, err = run_cmd(f"bash {e2e_script}", cwd=placeholders)
    print(out)
    if err:
        print(f"STDERR: {err}")
    if not ok:
        print("=== e2e_smoke.sh FAILED ===")
        print("FAIL")
        sys.exit(1)
    print("=== e2e_smoke.sh PASSED ===")

    # Step 2: Run semantic_validator on buyer dir
    buyer_dir = os.path.join(output, "buyer")
    if not os.path.exists(buyer_dir):
        print(f"ERROR: buyer dir not found at {buyer_dir}")
        print("FAIL")
        sys.exit(1)

    # Find semantic_validator
    for name in ["semantic_validator", "semantic_validator.py"]:
        validator = os.path.join(placeholders, name)
        if os.path.exists(validator):
            validator_cmd = f"python3 {validator}" if name.endswith(".py") else f"bash {validator}"
            break
    else:
        print("ERROR: semantic_validator not found")
        print("FAIL")
        sys.exit(1)

    print(f"=== Running semantic_validator on {buyer_dir} ===")
    ok, out, err = run_cmd(f"{validator_cmd} {buyer_dir}")
    print(out)
    if err:
        print(f"STDERR: {err}")
    if not ok:
        print("=== semantic_validator FAILED ===")
        print("FAIL")
        sys.exit(1)

    print("=== semantic_validator PASSED ===")
    print("PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
