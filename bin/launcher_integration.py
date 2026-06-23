#!/usr/bin/env python3
"""
OysterPlay Launcher Integration

This module provides integration between the route planner and OysterPlay launcher.
It reads the next route_type from route_planner.py and displays an on-screen banner.

Usage:
    python3 bin/launcher_integration.py --scene Overworld_NewWorld --batch-id 2026-05-batch-1
"""

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Dict

# Route type descriptions for on-screen display
ROUTE_TYPE_INFO = {
    1: {
        "name": "Normal Exploration A",
        "description": "Standard exploration pattern",
        "instructions": "Explore the area naturally, avoid repetitive movements."
    },
    2: {
        "name": "Normal Exploration B",
        "description": "Standard exploration variant",
        "instructions": "Similar to Type 1 but with different starting point."
    },
    3: {
        "name": "Special/Loop Pattern",
        "description": "Special pattern with loop routes",
        "instructions": "Follow the loop pattern, see manual for details."
    },
    4: {
        "name": "Rare Special Pattern",
        "description": "Rare special route pattern",
        "instructions": "Follow the rare pattern, see manual for details."
    }
}


def get_next_route_type(scene: str, batch_id: str) -> Dict[str, Any]:
    """
    Call route_planner.py to get the next route type.
    
    Returns:
        Dict with next_route_type, reason, and session_count_so_far
    """
    planner_path = Path(__file__).parent / "route_planner.py"
    
    result = subprocess.run(
        ["python3", str(planner_path), "--scene", scene, "--batch-id", batch_id],
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        raise RuntimeError(f"route_planner.py failed: {result.stderr}")
    
    return json.loads(result.stdout)


def generate_banner(route_type: int) -> str:
    """
    Generate an on-screen banner for the route type.
    
    Returns:
        Banner string for display
    """
    info = ROUTE_TYPE_INFO.get(route_type, {})
    name = info.get("name", "Unknown")
    description = info.get("description", "")
    instructions = info.get("instructions", "")
    
    banner = f"""
╔════════════════════════════════════════════════════════════════╗
║  ROUTE TYPE {route_type} — {name.upper():^48} ║
╠════════════════════════════════════════════════════════════════╣
║  {description:<60}  ║
║  {instructions:<60}  ║
╚════════════════════════════════════════════════════════════════╝
"""
    return banner


def generate_overlay_text(route_type: int) -> str:
    """
    Generate short overlay text for in-game display.
    
    Returns:
        Short text suitable for in-game overlay
    """
    info = ROUTE_TYPE_INFO.get(route_type, {})
    name = info.get("name", "Unknown")
    
    if route_type in [3, 4]:
        return f"ROUTE TYPE {route_type} — special pattern, see manual."
    else:
        return f"ROUTE TYPE {route_type} — {name}"


def main():
    parser = argparse.ArgumentParser(
        description="OysterPlay Launcher Integration"
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
        "--show-banner",
        action="store_true",
        help="Display the full banner"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON for programmatic use"
    )
    
    args = parser.parse_args()
    
    # Get next route type
    planner_result = get_next_route_type(args.scene, args.batch_id)
    route_type = planner_result["next_route_type"]
    
    if args.json:
        # Output JSON for programmatic use
        output = {
            "route_type": route_type,
            "overlay_text": generate_overlay_text(route_type),
            "planner_result": planner_result
        }
        print(json.dumps(output, indent=2))
    elif args.show_banner:
        # Display full banner
        print(generate_banner(route_type))
        print(f"Reason: {planner_result['reason']}")
    else:
        # Default: just show overlay text
        print(generate_overlay_text(route_type))


if __name__ == "__main__":
    main()
