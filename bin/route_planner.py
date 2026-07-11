#!/usr/bin/env python3
"""
Route Planner - Assigns route_type to sessions based on scene quota tracking.

Usage:
    python3 bin/route_planner.py --scene Overworld_NewWorld --batch-id 2026-05-batch-1
"""

import argparse
import json
from pathlib import Path
from typing import Any, Dict

# Default quota per route_type (can be overridden per scene)
DEFAULT_QUOTA = {
    "1": 10,  # Normal exploration
    "2": 10,  # Normal exploration variant
    "3": 10,  # Special/loop route
    "4": 5    # Special/loop route (rare)
}

# Scene-specific quota overrides
SCENE_QUOTAS = {
    "Overworld_NewWorld": {"1": 10, "2": 10, "3": 10, "4": 5},
    "Underground_Cave1": {"1": 8, "2": 8, "3": 12, "4": 4},
    "Sky_Island": {"1": 5, "2": 5, "3": 15, "4": 5},
}

# Base directory for batch files
BATCH_DIR = Path(__file__).parent.parent


def get_batch_manifest_path(batch_id: str) -> Path:
    """Get the path to the batch manifest file."""
    return BATCH_DIR / "batch_manifest.json"


def load_batch_manifest(batch_id: str) -> Dict[str, Any]:
    """Load existing batch manifest or create a new one."""
    manifest_path = get_batch_manifest_path(batch_id)
    
    if manifest_path.exists():
        with open(manifest_path, 'r') as f:
            return json.load(f)
    
    # Create new manifest
    return {
        "batch_id": batch_id,
        "scene": "",
        "operator_id": "",
        "quota": DEFAULT_QUOTA.copy(),
        "sessions": []
    }


def save_batch_manifest(manifest: Dict[str, Any]) -> None:
    """Save batch manifest to disk."""
    manifest_path = get_batch_manifest_path(manifest["batch_id"])
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)


def get_scene_quota(scene: str) -> Dict[str, int]:
    """Get quota for a specific scene."""
    return SCENE_QUOTAS.get(scene, DEFAULT_QUOTA)


def count_sessions_by_route_type(manifest: Dict[str, Any]) -> Dict[str, int]:
    """Count sessions per route_type in the manifest."""
    counts = {"1": 0, "2": 0, "3": 0, "4": 0}
    
    for session in manifest.get("sessions", []):
        route_type = str(session.get("route_type", 0))
        if route_type in counts:
            counts[route_type] += 1
    
    return counts


def pick_next_route_type(scene: str, manifest: Dict[str, Any]) -> tuple:
    """
    Pick the next route_type based on quota deficit.
    
    Returns:
        tuple: (route_type, reason, session_counts)
    """
    quota = get_scene_quota(scene)
    current_counts = count_sessions_by_route_type(manifest)
    
    # Calculate deficit for each route_type
    deficits = {}
    for route_type, target in quota.items():
        current = current_counts.get(route_type, 0)
        deficits[route_type] = target - current
    
    # Find route_type with highest deficit
    max_deficit = -1
    selected_route_type = 1  # Default to normal exploration
    
    for route_type, deficit in deficits.items():
        if deficit > max_deficit:
            max_deficit = deficit
            selected_route_type = int(route_type)
    
    # If all quotas met, pick route_type with lowest count
    if max_deficit <= 0:
        min_count = float('inf')
        for route_type, count in current_counts.items():
            if count < min_count:
                min_count = count
                selected_route_type = int(route_type)
    
    # Build reason string
    route_type_str = str(selected_route_type)
    current = current_counts.get(route_type_str, 0)
    target = quota.get(route_type_str, 10)
    
    if current < target:
        reason = (
            f"scene quota: route_type={selected_route_type} "
            f"has only {current} of {target} needed"
        )
    else:
        reason = f"quota met for all types, picking route_type={selected_route_type} (lowest count)"
    
    return selected_route_type, reason, current_counts


def main():
    parser = argparse.ArgumentParser(
        description="Route Planner - Assigns route_type based on scene quota"
    )
    parser.add_argument(
        "--scene",
        required=True,
        help="Scene name (e.g., Overworld_NewWorld)"
    )
    parser.add_argument(
        "--batch-id",
        required=True,
        help="Batch identifier (e.g., 2026-05-batch-1)"
    )
    parser.add_argument(
        "--operator-id",
        default="",
        help="Operator identifier"
    )
    parser.add_argument(
        "--record-session",
        action="store_true",
        help="Record this assignment in the manifest"
    )
    parser.add_argument(
        "--session-id",
        default="",
        help="Session ID to record (used with --record-session)"
    )
    
    args = parser.parse_args()
    
    # Load or create manifest
    manifest = load_batch_manifest(args.batch_id)
    
    # Update manifest metadata if provided
    if args.scene:
        manifest["scene"] = args.scene
    if args.operator_id:
        manifest["operator_id"] = args.operator_id
    
    # Set quota based on scene
    manifest["quota"] = get_scene_quota(args.scene)
    
    # Pick next route type
    route_type, reason, session_counts = pick_next_route_type(args.scene, manifest)
    
    # Record session if requested
    if args.record_session and args.session_id:
        session_entry = {
            "id": args.session_id,
            "route_type": route_type,
            "grade": "PENDING",
            "duration_s": 0,
            "audit_score": "0/0",
            "uploaded": False
        }
        manifest["sessions"].append(session_entry)
        save_batch_manifest(manifest)
    
    # Output result as JSON
    result = {
        "next_route_type": route_type,
        "reason": reason,
        "session_count_so_far": session_counts
    }
    
    print(json.dumps(result, indent=2))
    
    # Save manifest if it's new or updated
    save_batch_manifest(manifest)


if __name__ == "__main__":
    main()
