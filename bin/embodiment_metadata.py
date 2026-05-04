#!/usr/bin/env python3
"""
bin/embodiment_metadata.py

Generate per-scene embodiment.json metadata files containing embodiment_id,
agent_geometry, and locomotion_mode attributes.

Reference: arxiv 2505.05753 scaling-laws axis for embodiment characterization.
"""
from __future__ import annotations
import argparse
import json
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass
class AgentGeometry:
    """Physical geometry specification for an embodied agent."""
    height: float
    width: float
    depth: float
    mass: float
    bounding_box_type: str = "axis_aligned"


@dataclass
class LocomotionParams:
    """Locomotion mode parameters per arxiv 2505.05753 scaling-laws axis."""
    mode: str  # wheeled, legged, aerial, static
    max_speed: float
    turn_radius: float
    terrain_capability: str = "flat"


@dataclass
class EmbodimentMetadata:
    """Complete embodiment metadata for a scene."""
    embodiment_id: str
    scene_id: str
    agent_geometry: dict
    locomotion_mode: dict
    version: str = "1.0"


def generate_embodiment_id(scene_id: str) -> str:
    """Generate a unique embodiment ID for a scene."""
    return f"emb_{scene_id}_{uuid.uuid4().hex[:8]}"


def create_default_geometry() -> AgentGeometry:
    """Create default agent geometry configuration."""
    return AgentGeometry(height=1.75, width=0.6, depth=0.4, mass=70.0)


def create_default_locomotion() -> LocomotionParams:
    """Create default locomotion parameters."""
    return LocomotionParams(mode="legged", max_speed=1.5, turn_radius=0.3)


def generate_scene_metadata(
    scene_id: str,
    geometry: Optional[AgentGeometry] = None,
    locomotion: Optional[LocomotionParams] = None
) -> EmbodimentMetadata:
    """Generate embodiment metadata for a single scene."""
    geometry = geometry or create_default_geometry()
    locomotion = locomotion or create_default_locomotion()
    return EmbodimentMetadata(
        embodiment_id=generate_embodiment_id(scene_id),
        scene_id=scene_id,
        agent_geometry=asdict(geometry),
        locomotion_mode=asdict(locomotion)
    )


def process_scene_directory(
    scene_dir: Path,
    output_dir: Optional[Path] = None
) -> list[EmbodimentMetadata]:
    """Process all scenes in a directory and generate metadata."""
    results = []
    scene_dir = scene_dir.resolve()
    if not scene_dir.exists():
        print(f"Warning: Scene directory not found: {scene_dir}", file=sys.stderr)
        return results
    for entry in scene_dir.iterdir():
        if entry.is_dir():
            scene_id = entry.name
            metadata = generate_scene_metadata(scene_id)
            results.append(metadata)
            if output_dir:
                output_dir.mkdir(parents=True, exist_ok=True)
                out_file = output_dir / f"{scene_id}_embodiment.json"
                with open(out_file, "w", encoding="utf-8") as f:
                    json.dump(asdict(metadata), f, indent=2)
                print(f"Written: {out_file}")
    return results


def main(argv: Optional[list[str]] = None) -> int:
    """Main entry point with argparse CLI."""
    parser = argparse.ArgumentParser(
        description="Generate per-scene embodiment.json metadata files."
    )
    parser.add_argument(
        "--scene-dir", type=Path, required=True,
        help="Directory containing scene subdirectories"
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Output directory for embodiment.json files"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print metadata without writing files"
    )
    args = parser.parse_args(argv)
    metadata_list = process_scene_directory(
        args.scene_dir,
        output_dir=None if args.dry_run else args.output
    )
    if args.dry_run:
        for meta in metadata_list:
            print(json.dumps(asdict(meta), indent=2))
    print(f"Processed {len(metadata_list)} scenes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())