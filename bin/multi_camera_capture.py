#!/usr/bin/env python3
"""
Multi-Camera Concurrent Capture for Habitat-Sim Parity.

Cluster C+: multi-camera concurrent capture (1st-person + 3rd-person + top-down)
for buyers training multi-view world models.

Usage:
    python bin/multi_camera_capture.py --output-dir ./captures --fps 30 --frames 100
    python bin/multi_camera_capture.py --config rig.json --dry-run
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import sys
import tempfile
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)


class CameraType(Enum):
    """Camera viewpoint types for multi-view capture."""
    FIRST_PERSON = "first_person"
    THIRD_PERSON = "third_person"
    TOP_DOWN = "top_down"


@dataclass
class CameraConfig:
    """Configuration for a single camera in the capture rig."""
    name: str
    camera_type: CameraType
    width: int = 640
    height: int = 480
    fov: float = 90.0
    position: Tuple[float, float, float] = (0.0, 1.5, 0.0)
    rotation: Tuple[float, float, float] = (0.0, 0.0, 0.0)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize camera config to a JSON-compatible dictionary."""
        return {
            "name": self.name,
            "camera_type": self.camera_type.value,
            "width": self.width,
            "height": self.height,
            "fov": self.fov,
            "position": list(self.position),
            "rotation": list(self.rotation),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CameraConfig":
        """Deserialize a CameraConfig from a JSON-compatible dictionary."""
        return cls(
            name=data["name"],
            camera_type=CameraType(data["camera_type"]),
            width=data.get("width", 640),
            height=data.get("height", 480),
            fov=data.get("fov", 90.0),
            position=tuple(data.get("position", [0.0, 1.5, 0.0])),
            rotation=tuple(data.get("rotation", [0.0, 0.0, 0.0])),
        )


@dataclass
class CaptureSettings:
    """Global settings for a multi-camera capture session."""
    output_dir: Path
    fps: int = 30
    image_format: str = "png"
    max_frames: Optional[int] = None
    duration: Optional[float] = None
    save_metadata: bool = True
    simulate_delay: bool = True
    num_workers: int = 3

    def validate(self) -> None:
        """Validate capture settings, raising ValueError on bad input."""
        if self.fps <= 0:
            raise ValueError(f"FPS must be positive, got {self.fps}")
        if self.max_frames is not None and self.max_frames <= 0:
            raise ValueError(f"max_frames must be positive, got {self.max_frames}")
        if self.duration is not None and self.duration <= 0:
            raise ValueError(f"duration must be positive, got {self.duration}")
        if self.num_workers < 1:
            raise ValueError(f"num_workers must be >= 1, got {self.num_workers}")
        self.output_dir.mkdir(parents=True, exist_ok=True)


def build_default_rig() -> List[CameraConfig]:
    """Build a default 3-camera rig: 1st-person, 3rd-person, top-down."""
    return [
        CameraConfig(
            "fpv", CameraType.FIRST_PERSON, 640, 480, 90.0,
            (0.0, 1.5, 0.0), (0.0, 0.0, 0.0),
        ),
        CameraConfig(
            "tpv", CameraType.THIRD_PERSON, 640, 480, 75.0,
            (0.0, 3.0, -5.0), (-15.0, 0.0, 0.0),
        ),
        CameraConfig(
            "top", CameraType.TOP_DOWN, 512, 512, 60.0,
            (0.0, 10.0, 0.0), (-90.0, 0.0, 0.0),
        ),
    ]


def load_rig_config(path: Path) -> List[CameraConfig]:
    """Load camera rig configuration from a JSON file."""
    with open(path, "r") as fh:
        data = json.load(fh)
    if isinstance(data, list):
        return [CameraConfig.from_dict(c) for c in data]
    if isinstance(data, dict) and "cameras" in data:
        return [CameraConfig.from_dict(c) for c in data["cameras"]]
    raise ValueError(f"Unrecognized rig config format in {path}")


def generate_synthetic_frame(
    camera: CameraConfig,
    frame_idx: int,
    timestamp: float,
) -> Image.Image:
    """Generate a synthetic placeholder frame for a camera view.

    In production this would call Habitat-Sim's render pipeline.
    Here we produce a labeled PIL image for offline testing and
    dry-run validation.
    """
    img = Image.new("RGB", (camera.width, camera.height), color=(30, 30, 30))
    draw = ImageDraw.Draw(img)
    label = (
        f"{camera.camera_type.value}\n"
        f"frame={frame_idx}  t={timestamp:.3f}s\n"
        f"{camera.width}x{camera.height}  fov={camera.fov}"
    )
    draw.text((10, 10), label, fill=(255, 255, 255))
    # Draw a simple crosshair at center
    cx, cy = camera.width // 2, camera.height // 2
    draw.line((cx - 15, cy, cx + 15, cy), fill=(0, 255, 0), width=2)
    draw.line((cx, cy - 15, cx, cy + 15), fill=(0, 255, 0), width=2)
    return img


def capture_single_frame(
    camera: CameraConfig,
    frame_idx: int,
    timestamp: float,
    output_dir: Path,
    image_format: str,
    simulate_delay: bool = True,
) -> Dict[str, Any]:
    """Capture (simulate) a single frame for one camera and save to disk."""
    start = time.monotonic()
    img = generate_synthetic_frame(camera, frame_idx, timestamp)
    frame_dir = output_dir / camera.name
    frame_dir.mkdir(parents=True, exist_ok=True)
    filename = f"frame_{frame_idx:06d}.{image_format}"
    filepath = frame_dir / filename
    img.save(str(filepath))
    elapsed = time.monotonic() - start
    if simulate_delay:
        time.sleep(max(0.0, 0.001 - elapsed))
    logger.debug("Saved %s (%.1f ms)", filepath, elapsed * 1000)
    return {
        "camera": camera.name,
        "frame": frame_idx,
        "path": str(filepath),
        "elapsed_ms": round(elapsed * 1000, 2),
    }


def run_capture(
    rig: List[CameraConfig],
    settings: CaptureSettings,
    dry_run: bool = False,
) -> List[Dict[str, Any]]:
    """Execute the multi-camera capture loop.

    Uses a thread-pool executor to capture all cameras concurrently
    per frame tick, matching real-world multi-sensor rigs.
    """
    settings.validate()
    total_frames = settings.max_frames or 100
    results: List[Dict[str, Any]] = []
    meta: Dict[str, Any] = {
        "rig": [c.to_dict() for c in rig],
        "settings": {
            "fps": settings.fps,
            "image_format": settings.image_format,
            "total_frames": total_frames,
        },
        "frames": [],
    }
    interval = 1.0 / settings.fps

    logger.info(
        "Starting capture: %d cameras, %d frames @ %d fps",
        len(rig), total_frames, settings.fps,
    )

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=settings.num_workers,
    ) as executor:
        for frame_idx in range(total_frames):
            timestamp = frame_idx * interval
            if dry_run:
                logger.info(
                    "[dry-run] frame %d/%d  t=%.3fs",
                    frame_idx + 1, total_frames, timestamp,
                )
                meta["frames"].append({
                    "frame": frame_idx,
                    "timestamp": round(timestamp, 4),
                    "cameras": [c.name for c in rig],
                })
                continue

            futures = {
                executor.submit(
                    capture_single_frame,
                    cam, frame_idx, timestamp,
                    settings.output_dir,
                    settings.image_format,
                    settings.simulate_delay,
                ): cam.name
                for cam in rig
            }
            frame_results = []
            for fut in concurrent.futures.as_completed(futures):
                try:
                    frame_results.append(fut.result())
                except Exception as exc:
                    logger.error("Camera %s failed: %s", futures[fut], exc)
            results.extend(frame_results)
            meta["frames"].append({
                "frame": frame_idx,
                "timestamp": round(timestamp, 4),
                "cameras": [r["camera"] for r in frame_results],
            })

    if settings.save_metadata and not dry_run:
        meta_path = settings.output_dir / "capture_meta.json"
        with open(meta_path, "w") as fh:
            json.dump(meta, fh, indent=2)
        logger.info("Metadata written to %s", meta_path)

    return results


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Multi-camera concurrent capture for Habitat-Sim parity.",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Directory to save captured frames (default: temp dir).",
    )
    parser.add_argument(
        "--config", type=Path, default=None,
        help="Path to JSON rig config file (default: built-in 3-camera rig).",
    )
    parser.add_argument(
        "--fps", type=int, default=30,
        help="Frames per second (default: 30).",
    )
    parser.add_argument(
        "--frames", type=int, default=100,
        help="Total number of frames to capture (default: 100).",
    )
    parser.add_argument(
        "--format", dest="image_format", type=str, default="png",
        choices=["png", "jpeg"],
        help="Image output format (default: png).",
    )
    parser.add_argument(
        "--workers", type=int, default=3,
        help="Number of concurrent capture workers (default: 3).",
    )
    parser.add_argument(
        "--no-delay", action="store_true",
        help="Disable simulated per-frame delay.",
    )
    parser.add_argument(
        "--no-metadata", action="store_true",
        help="Skip writing capture_meta.json.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Simulate capture without writing image files.",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point for the multi-camera capture CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # Resolve output directory
    if args.output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="multi_capture_"))
        logger.info("No --output-dir given; using temp dir %s", output_dir)
    else:
        output_dir = args.output_dir

    # Load rig configuration
    if args.config is not None:
        rig = load_rig_config(args.config)
    else:
        rig = build_default_rig()

    settings = CaptureSettings(
        output_dir=output_dir,
        fps=args.fps,
        image_format=args.image_format,
        max_frames=args.frames,
        save_metadata=not args.no_metadata,
        simulate_delay=not args.no_delay,
        num_workers=args.workers,
    )

    try:
        run_capture(rig, settings, dry_run=args.dry_run)
    except Exception as exc:
        logger.error("Capture failed: %s", exc)
        return 1

    if args.dry_run:
        logger.info("Dry-run complete — no files written.")
    else:
        logger.info(
            "Capture complete: %d frames across %d cameras → %s",
            args.frames, len(rig), output_dir,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
