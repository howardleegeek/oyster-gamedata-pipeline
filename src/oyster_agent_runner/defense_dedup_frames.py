#!/usr/bin/env python3
"""
Defense mechanism for detecting and rejecting duplicate frame IDs within a scene.

This module provides an in-memory set-based tracker that identifies duplicate
frame_id submissions within a single scene execution context. It's designed
to prevent replay attacks or accidental duplicate frame processing in the
oyster agent runner system.

Author: Blue Team
Date: 2024
Version: 1.0.0
"""

import argparse
import json
import logging
import sys
from typing import Any

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class FrameDeduplicationDefense:
    """
    In-memory defense mechanism for tracking and rejecting duplicate frame IDs.

    This class maintains a set of seen frame IDs within a scene context and
    provides methods to check for duplicates, add new frames, and clear the
    tracking state between scenes.

    Attributes:
        seen_frame_ids: Set of frame IDs that have been processed in current scene
        scene_id: Optional identifier for the current scene being tracked
    """

    def __init__(self, scene_id: str | None = None):
        """
        Initialize a new frame deduplication defense instance.

        Args:
            scene_id: Optional identifier for the scene being tracked
        """
        self.seen_frame_ids: set[str] = set()
        self.scene_id = scene_id
        logger.info(f"Initialized FrameDeduplicationDefense for scene: {scene_id}")

    def check_duplicate(self, frame_id: str) -> bool:
        """
        Check if a frame ID has already been seen in the current scene.

        Args:
            frame_id: The frame ID to check

        Returns:
            True if the frame ID is a duplicate, False otherwise
        """
        is_duplicate = frame_id in self.seen_frame_ids

        if is_duplicate:
            logger.warning(f"Duplicate frame_id detected: {frame_id} (scene: {self.scene_id})")
        else:
            logger.debug(f"New frame_id: {frame_id}")

        return is_duplicate

    def add_frame(self, frame_id: str) -> None:
        """
        Add a frame ID to the tracking set.

        Args:
            frame_id: The frame ID to add to the tracking set

        Raises:
            ValueError: If frame_id is empty or None
        """
        if not frame_id:
            raise ValueError("frame_id cannot be empty or None")

        self.seen_frame_ids.add(frame_id)
        logger.debug(f"Added frame_id to tracking: {frame_id}")

    def process_frame(self, frame_id: str, frame_data: dict[str, Any] | None = None) -> bool:
        """
        Process a frame by checking for duplicates and adding it if new.

        This is a convenience method that combines check_duplicate and add_frame.

        Args:
            frame_id: The frame ID to process
            frame_data: Optional frame data (not used for dedup, for API compatibility)

        Returns:
            True if the frame is a duplicate and should be rejected, False otherwise
        """
        if self.check_duplicate(frame_id):
            return True  # Reject duplicate

        self.add_frame(frame_id)
        return False  # Accept new frame

    def clear(self) -> None:
        """
        Clear all tracked frame IDs, typically called between scenes.
        """
        count = len(self.seen_frame_ids)
        self.seen_frame_ids.clear()
        logger.info(f"Cleared {count} tracked frame_ids (scene: {self.scene_id})")

    def get_stats(self) -> dict[str, Any]:
        """
        Get statistics about the current tracking state.

        Returns:
            Dictionary containing tracking statistics
        """
        return {
            "scene_id": self.scene_id,
            "unique_frames_seen": len(self.seen_frame_ids),
            "frame_ids": sorted(self.seen_frame_ids),
        }

    def reset_scene(self, new_scene_id: str | None = None) -> None:
        """
        Reset the defense for a new scene.

        Args:
            new_scene_id: Optional new scene ID (if None, keeps current)
        """
        if new_scene_id is not None:
            self.scene_id = new_scene_id

        self.clear()
        logger.info(f"Reset defense for new scene: {self.scene_id}")


def parse_arguments() -> argparse.Namespace:
    """
    Parse command line arguments.

    Returns:
        Parsed arguments namespace
    """
    parser = argparse.ArgumentParser(
        description="Frame deduplication defense for oyster agent runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --scene-id scene_001 --frame-id frame_001 --frame-id frame_002
  %(prog)s --check-duplicates --input-file frames.json
  %(prog)s --stats-only --scene-id scene_001
        """,
    )

    parser.add_argument("--scene-id", type=str, help="Scene identifier for tracking context")

    parser.add_argument(
        "--frame-id",
        type=str,
        action="append",
        default=[],
        help="Frame ID to process (can be specified multiple times)",
    )

    parser.add_argument("--input-file", type=str, help="JSON file containing frame IDs to process")

    parser.add_argument(
        "--check-duplicates", action="store_true", help="Check for duplicates in provided frame IDs"
    )

    parser.add_argument(
        "--stats-only", action="store_true", help="Only show statistics, don't process frames"
    )

    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")

    parser.add_argument(
        "--output-format",
        type=str,
        choices=["text", "json"],
        default="text",
        help="Output format for results",
    )

    return parser.parse_args()


def load_frame_ids_from_file(file_path: str) -> list[str]:
    """
    Load frame IDs from a JSON file.

    Args:
        file_path: Path to JSON file

    Returns:
        List of frame IDs

    Raises:
        FileNotFoundError: If file doesn't exist
        json.JSONDecodeError: If file contains invalid JSON
    """
    try:
        with open(file_path) as f:
            data = json.load(f)

        # Support multiple formats: list of IDs or dict with frame_ids key
        if isinstance(data, list):
            return [str(item) for item in data]
        elif isinstance(data, dict) and "frame_ids" in data:
            return [str(item) for item in data["frame_ids"]]
        else:
            raise ValueError(
                f"Invalid JSON structure in {file_path}. "
                f"Expected list or dict with 'frame_ids' key."
            )
    except Exception as e:
        logger.error(f"Failed to load frame IDs from {file_path}: {e}")
        raise


def main(argv: list[str] | None = None) -> int:
    """
    Main entry point for the frame deduplication defense.

    Args:
        argv: Command line arguments (defaults to sys.argv[1:])

    Returns:
        Exit code (0 for success, non-zero for error)
    """
    if argv is None:
        argv = sys.argv[1:]

    args = parse_arguments()

    # Configure logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Initialize defense
    defense = FrameDeduplicationDefense(scene_id=args.scene_id)

    # Load frame IDs from input file if specified
    frame_ids = list(args.frame_id)
    if args.input_file:
        try:
            file_frame_ids = load_frame_ids_from_file(args.input_file)
            frame_ids.extend(file_frame_ids)
        except Exception as e:
            logger.error(f"Failed to process input file: {e}")
            return 1

    # If stats only, just show stats and exit
    if args.stats_only:
        stats = defense.get_stats()
        if args.output_format == "json":
            print(json.dumps(stats, indent=2))
        else:
            print(f"Scene ID: {stats['scene_id']}")
            print(f"Unique frames seen: {stats['unique_frames_seen']}")
            print(f"Frame IDs: {', '.join(stats['frame_ids'])}")
        return 0

    # Process frame IDs
    duplicates = []
    new_frames = []

    for frame_id in frame_ids:
        if defense.check_duplicate(frame_id):
            duplicates.append(frame_id)
        else:
            defense.add_frame(frame_id)
            new_frames.append(frame_id)

    # Output results
    if args.output_format == "json":
        result = {
            "scene_id": args.scene_id,
            "total_frames_processed": len(frame_ids),
            "duplicates_found": len(duplicates),
            "new_frames_added": len(new_frames),
            "duplicate_frame_ids": duplicates,
            "new_frame_ids": new_frames,
            "stats": defense.get_stats(),
        }
        print(json.dumps(result, indent=2))
    else:
        print(f"Scene: {args.scene_id}")
        print(f"Total frames processed: {len(frame_ids)}")
        print(f"Duplicates found: {len(duplicates)}")
        print(f"New frames added: {len(new_frames)}")

        if duplicates:
            print("\nDuplicate frame IDs:")
            for dup in duplicates:
                print(f"  - {dup}")

        if new_frames:
            print("\nNew frame IDs:")
            for new in new_frames:
                print(f"  - {new}")

        stats = defense.get_stats()
        print(f"\nCurrent unique frames in scene: {stats['unique_frames_seen']}")

    # Return non-zero exit code if duplicates were found (for scripting)
    return 1 if duplicates else 0


if __name__ == "__main__":
    sys.exit(main())
