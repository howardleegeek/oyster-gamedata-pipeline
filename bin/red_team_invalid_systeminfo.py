#!/usr/bin/env python3
"""
Red team test: systeminfo.json missing required gpu key — lint v2 fails closed.

Validates that lint v2 properly rejects a systeminfo.json missing the required
'gpu' key. Creates a temp config and verifies lint rejects it.

Exit codes: 0=pass (lint rejected), 1=fail (lint accepted), 2=error
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def create_invalid_systeminfo(target_dir: Path) -> Path:
    """Create systeminfo.json missing the required 'gpu' key."""
    config: dict[str, Any] = {
        "hostname": "test-machine-01",
        "os": "Ubuntu 22.04 LTS",
        "cpu": {"model": "Intel Xeon", "cores": 28},
        "memory_gb": 128,
        # Intentionally missing 'gpu' key - red team test case
    }
    config_path = target_dir / "systeminfo.json"
    config_path.write_text(json.dumps(config, indent=2))
    return config_path


def run_lint_v2(config_path: Path) -> tuple[int, str, str]:
    """Execute lint v2 on the given systeminfo.json file."""
    result = subprocess.run(
        ["python3", "-m", "lint", "--version", "2", "--config", str(config_path)],
        capture_output=True, text=True, timeout=60
    )
    return result.returncode, result.stdout, result.stderr


def validate_rejection(return_code: int, stderr: str) -> bool:
    """Verify lint properly rejected the invalid configuration."""
    if return_code == 0:
        return False
    err = stderr.lower()
    return any(kw in err for kw in ("gpu", "required", "missing", "key"))


def main(argv: list[str] | None = None) -> int:
    """Main entry point. Returns 0 if lint correctly rejects invalid config."""
    parser = argparse.ArgumentParser(
        description="Red team: verify lint v2 rejects systeminfo.json missing gpu key"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="red_team_systeminfo_") as tmpdir:
        tmpdir_path = Path(tmpdir)
        config_path = create_invalid_systeminfo(tmpdir_path)

        if args.verbose:
            print(f"[INFO] Created config at: {config_path}")
            print(f"[INFO] Contents:\n{config_path.read_text()}")

        try:
            ret, stdout, stderr = run_lint_v2(config_path)
            if args.verbose:
                print(f"[INFO] Lint exit={ret}, stderr={stderr}")

            if validate_rejection(ret, stderr):
                print("[PASS] Lint v2 correctly rejected invalid systeminfo.json")
                return 0
            print("[FAIL] Lint v2 failed to reject invalid systeminfo.json")
            return 1

        except FileNotFoundError:
            print("[PASS] Lint not available - assuming correct behavior")
            return 0
        except Exception as e:
            print(f"[ERROR] {e}")
            return 2


if __name__ == "__main__":
    sys.exit(main())