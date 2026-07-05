#!/usr/bin/env python3
"""
ZBuffer integration test.

If mod patch deployed: assert depth/.source kind=engine_zbuffer + H8 PASS in audit.
Otherwise: mark SKIP with reason "mod patch not yet deployed; run after deployment".
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def check_mod_patch_deployed() -> bool:
    """Check if zbuffer mod patch is deployed on minipc1."""
    # Check for mod patch indicator file or environment variable
    # This could check for:
    # - A marker file on minipc1
    # - An environment variable
    # - The presence of zbuffer-specific files in the session
    
    # For now, check if there's a marker indicating deployment
    # In production, this would SSH to minipc1 and check
    return os.environ.get("ZBUFFER_MOD_DEPLOYED", "false").lower() == "true"


def find_depth_source_marker(session_dir: str) -> Optional[Dict[str, Any]]:
    """Find and parse depth/.source marker."""
    # Look for depth/.source in common locations
    possible_paths = [
        Path(session_dir) / "depth" / ".source",
        Path(session_dir) / "depth.source",
        Path(session_dir) / ".source",
        Path(session_dir) / "source.json",
    ]
    
    for path in possible_paths:
        if path.exists():
            try:
                with open(path) as f:
                    return json.load(f)
            except Exception as e:
                logger.debug("Failed to load %s: %s", path, e)
    
    return None


def run_audit_with_zbuffer_check(session_dir: str) -> Dict[str, Any]:
    """Run audit to check for H8 zbuffer pass."""
    audit_script = Path(__file__).parent.parent / "audit.py"
    
    if not audit_script.exists():
        return {"status": "SKIP", "evidence": "audit.py not found"}
    
    try:
        result = subprocess.run(
            [sys.executable, str(audit_script), "--check", "H8"],
            cwd=session_dir,
            capture_output=True,
            text=True,
            timeout=120
        )
        
        output = result.stdout + result.stderr
        
        if "PASS" in output or result.returncode == 0:
            return {"status": "PASS", "evidence": "H8 audit PASS"}
        else:
            return {"status": "FAIL", "evidence": f"H8 audit failed: {output[:200]}"}
    except Exception as e:
        logger.debug("Audit subprocess failed: %s", e)
        return {"status": "FAIL", "evidence": str(e)}


def validate_zbuffer_marker(source_marker: Dict[str, Any]) -> Dict[str, Any]:
    """Validate depth/.source marker has correct zbuffer kind."""
    kind = source_marker.get("kind", "")
    
    if kind == "engine_zbuffer":
        return {"status": "PASS", "evidence": "kind=engine_zbuffer confirmed"}
    else:
        return {"status": "FAIL", "evidence": f"expected kind=engine_zbuffer, got kind={kind}"}


def main():
    parser = argparse.ArgumentParser(description="ZBuffer integration test")
    parser.add_argument("--session-dir", required=True, help="Session directory")
    parser.add_argument("--force", action="store_true", help="Force test even if patch not deployed")
    args = parser.parse_args()
    
    # Check if mod patch is deployed (unless --force)
    if not args.force and not check_mod_patch_deployed():
        print("SKIP: mod patch not yet deployed; run after deployment")
        sys.exit(0)
    
    # Find and validate depth source marker
    marker = find_depth_source_marker(args.session_dir)
    if not marker:
        print("FAIL: no depth source marker found")
        sys.exit(1)
    
    marker_result = validate_zbuffer_marker(marker)
    if marker_result["status"] != "PASS":
        print(f"FAIL: {marker_result['evidence']}")
        sys.exit(1)
    
    # Run audit check
    audit_result = run_audit_with_zbuffer_check(args.session_dir)
    if audit_result["status"] == "PASS":
        print(f"PASS: {audit_result['evidence']}")
        sys.exit(0)
    elif audit_result["status"] == "SKIP":
        print(f"SKIP: {audit_result['evidence']}")
        sys.exit(0)
    else:
        print(f"FAIL: {audit_result['evidence']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
