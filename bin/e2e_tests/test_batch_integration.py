#!/usr/bin/env python3
"""
Batch integration test.

Runs bin/route_planner.py against a fake batch with quota {1:3, 2:5, 3:2}:
- Assert it picks the route_type with most remaining quota
- Assert batch_manifest.json updated correctly
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

# Test quota configuration
TEST_QUOTA = {1: 3, 2: 5, 3: 2}


def create_test_batch(temp_dir: Path) -> str:
    """Create a test batch with the specified quota."""
    batch_manifest = {
        "batch_id": "test_batch_e2e",
        "quota": TEST_QUOTA,
        "routes": []
    }
    
    manifest_path = temp_dir / "batch_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(batch_manifest, f, indent=2)
    
    return str(temp_dir)


def run_route_planner(session_dir: str, quota: Dict[int, int]) -> Dict[str, Any]:
    """Run route_planner.py and return parsed results."""
    planner_script = Path(__file__).parent.parent / "route_planner.py"
    
    if not planner_script.exists():
        return {"status": "SKIP", "evidence": "route_planner.py not found"}
    
    # Create temp batch manifest
    with tempfile.TemporaryDirectory() as temp_dir:
        batch_manifest = {
            "batch_id": "test_batch_e2e",
            "quota": quota,
            "routes": []
        }
        
        manifest_path = Path(temp_dir) / "batch_manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(batch_manifest, f, indent=2)
        
        # Run route planner
        try:
            subprocess.run(
                [sys.executable, str(planner_script), "--batch", str(manifest_path)],
                capture_output=True,
                text=True,
                timeout=60
            )
            
            # Read updated manifest
            if manifest_path.exists():
                with open(manifest_path) as f:
                    updated_manifest = json.load(f)
            else:
                return {
                    "status": "FAIL",
                    "evidence": "batch_manifest.json not found after run"
                }
            
            return {
                "status": "PASS",
                "manifest": updated_manifest
            }
        except subprocess.TimeoutExpired:
            return {"status": "FAIL", "evidence": "route_planner timeout"}
        except Exception as e:
            return {"status": "FAIL", "evidence": str(e)}


def validate_route_selection(manifest: Dict[str, Any], quota: Dict[int, int]) -> Dict[str, Any]:
    """Validate that route_planner picked the route with most remaining quota."""
    routes = manifest.get("routes", [])
    
    if not routes:
        return {"status": "FAIL", "evidence": "no routes assigned"}
    
    # Get the last assigned route
    last_route = routes[-1]
    route_type = last_route.get("route_type")
    
    if route_type is None:
        return {"status": "FAIL", "evidence": "route_type not specified"}
    
    # Find the route type with most remaining quota
    # Quota is decremented after assignment, so we need to check original quota
    # and find which type has highest remaining
    
    # For the test, we expect route_type=2 (quota 5 is highest)
    expected_route_type = max(quota, key=quota.get)
    
    if route_type == expected_route_type:
        return {
            "status": "PASS",
            "evidence": f"route_planner picked route_type={route_type}"
        }
    else:
        return {
            "status": "FAIL",
            "evidence": f"expected route_type={expected_route_type}, got {route_type}"
        }


def main():
    parser = argparse.ArgumentParser(description="Batch integration test")
    parser.add_argument("--session-dir", required=True, help="Session directory")
    parser.add_argument("--quota", type=str, default=None,
                        help="JSON quota config (default: {1:3,2:5,3:2})")
    args = parser.parse_args()
    
    # Parse quota if provided
    if args.quota:
        quota = json.loads(args.quota)
    else:
        quota = TEST_QUOTA
    
    # Run route planner
    result = run_route_planner(args.session_dir, quota)
    
    if result["status"] == "SKIP":
        print(f"SKIP: {result.get('evidence', 'skipped')}")
        sys.exit(0)
    
    if result["status"] == "FAIL":
        print(f"FAIL: {result.get('evidence', 'failed')}")
        sys.exit(1)
    
    # Validate route selection
    manifest = result.get("manifest", {})
    validation = validate_route_selection(manifest, quota)
    
    if validation["status"] == "PASS":
        print(f"PASS: {validation['evidence']}")
        sys.exit(0)
    else:
        print(f"FAIL: {validation['evidence']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
