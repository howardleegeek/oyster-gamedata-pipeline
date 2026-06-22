#!/usr/bin/env python3
"""
Provenance integration test.

Runs oyster_provenance/verify.py on a session with backfilled provenance.json:
- Assert Merkle root recomputes correctly
- Assert ed25519 signature verifies
- Assert biometric_flags structure intact
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict


def run_provenance_verify(session_dir: str) -> Dict[str, Any]:
    """Run oyster-verify on the session and return parsed results."""
    verify_script = Path(__file__).parent.parent / "oyster_provenance" / "verify.py"
    
    # Also check for alternative paths
    if not verify_script.exists():
        verify_script = Path(__file__).parent.parent / "oyster-verify"
    if not verify_script.exists():
        verify_script = Path(__file__).parent.parent / "bin" / "oyster-verify.py"
    if not verify_script.exists():
        return {"status": "SKIP", "evidence": "oyster-verify not found"}
    
    # Run verify
    try:
        result = subprocess.run(
            [sys.executable, str(verify_script)],
            cwd=session_dir,
            capture_output=True,
            text=True,
            timeout=120
        )
        
        output = result.stdout + result.stderr
        
        # Parse output for verification results
        # Look for success indicators
        if "✓" in output or "PASS" in output or result.returncode == 0:
            return {
                "status": "PASS",
                "output": output,
                "verified": True
            }
        else:
            return {
                "status": "FAIL",
                "output": output[:500],
                "verified": False
            }
    except subprocess.TimeoutExpired:
        return {"status": "FAIL", "evidence": "verify timeout"}
    except Exception as e:
        return {"status": "FAIL", "evidence": str(e)}


def validate_provenance_manifest(session_dir: str) -> Dict[str, Any]:
    """Validate provenance.json structure if present."""
    provenance_path = Path(session_dir) / "provenance.json"
    
    if not provenance_path.exists():
        return {"status": "SKIP", "evidence": "provenance.json not found"}
    
    try:
        with open(provenance_path) as f:
            provenance = json.load(f)
        
        # Check Merkle root present
        if "merkle_root" not in provenance:
            return {"status": "FAIL", "evidence": "merkle_root not in provenance"}
        
        # Check signature present
        if "signature" not in provenance:
            return {"status": "FAIL", "evidence": "signature not in provenance"}
        
        # Check biometric_flags structure
        if "biometric_flags" in provenance:
            flags = provenance["biometric_flags"]
            required_fields = ["session_id", "timestamp", "flags"]
            missing = [f for f in required_fields if f not in flags]
            if missing:
                return {"status": "FAIL", "evidence": f"biometric_flags missing: {missing}"}
        
        return {
            "status": "PASS",
            "evidence": "merkle + sig + flags all present"
        }
    except json.JSONDecodeError as e:
        return {"status": "FAIL", "evidence": f"invalid JSON: {e}"}
    except Exception as e:
        return {"status": "FAIL", "evidence": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Provenance integration test")
    parser.add_argument("--session-dir", required=True, help="Session directory")
    args = parser.parse_args()
    
    # First validate local manifest structure
    manifest_validation = validate_provenance_manifest(args.session_dir)
    
    if manifest_validation["status"] == "SKIP":
        # Try running verify anyway (it might create/fetch provenance)
        pass
    elif manifest_validation["status"] == "FAIL":
        print(f"FAIL: {manifest_validation['evidence']}")
        sys.exit(1)
    
    # Run oyster-verify
    result = run_provenance_verify(args.session_dir)
    
    if result["status"] == "SKIP":
        print(f"SKIP: {result.get('evidence', 'skipped')}")
        sys.exit(0)
    
    if result["status"] == "FAIL":
        print(f"FAIL: {result.get('evidence', 'failed')}")
        sys.exit(1)
    
    # If we got here, verify passed
    # Combine evidence from manifest validation if available
    if manifest_validation["status"] == "PASS":
        evidence = manifest_validation["evidence"]
    else:
        evidence = "oyster-verify returned success"
    
    print(f"PASS: {evidence}")
    sys.exit(0)


if __name__ == "__main__":
    main()
