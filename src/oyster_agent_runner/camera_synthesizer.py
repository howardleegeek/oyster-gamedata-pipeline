#!/usr/bin/env python3
"""
Camera Rotation Synthesizer - Independent synthesizer for camera rotation data.

Generates synthetic camera rotation trajectories for testing and simulation.
Per PDF p3 specification - this is a SYNTHESIZER, NOT a player.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import tempfile
from pathlib import Path


class CameraRotationSynthesizer:
    """Synthesizes camera rotation data for testing and simulation."""

    def __init__(self, seed: int | None = None) -> None:
        """Initialize synthesizer with optional random seed for reproducibility."""
        if seed is not None:
            random.seed(seed)

    @staticmethod
    def _random_unit_vector() -> tuple[float, float, float]:
        """Generate random unit vector uniformly distributed on sphere."""
        theta = random.uniform(0, 2 * math.pi)
        z = random.uniform(-1, 1)
        r = math.sqrt(1 - z * z)
        return (r * math.cos(theta), r * math.sin(theta), z)

    @staticmethod
    def _axis_angle_to_quaternion(
        axis: tuple[float, float, float], angle: float
    ) -> tuple[float, float, float, float]:
        """Convert axis-angle to quaternion (w, x, y, z)."""
        half = angle / 2
        s = math.sin(half)
        return (math.cos(half), axis[0] * s, axis[1] * s, axis[2] * s)

    @staticmethod
    def _quaternion_multiply(
        q1: tuple[float, float, float, float],
        q2: tuple[float, float, float, float],
    ) -> tuple[float, float, float, float]:
        """Multiply two quaternions."""
        w1, x1, y1, z1 = q1
        w2, x2, y2, z2 = q2
        return (
            w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
            w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        )

    @staticmethod
    def _normalize_quaternion(
        q: tuple[float, float, float, float],
    ) -> tuple[float, float, float, float]:
        """Normalize quaternion to unit length."""
        w, x, y, z = q
        mag = math.sqrt(w * w + x * x + y * y + z * z)
        return (w / mag, x / mag, y / mag, z / mag) if mag > 1e-10 else (1.0, 0.0, 0.0, 0.0)

    @staticmethod
    def _quaternion_to_matrix(q: tuple[float, float, float, float]) -> list[list[float]]:
        """Convert quaternion to 3x3 rotation matrix."""
        w, x, y, z = q
        return [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ]

    @staticmethod
    def _matrix_to_euler(m: list[list[float]]) -> tuple[float, float, float]:
        """Convert 3x3 rotation matrix to Euler angles (roll, pitch, yaw)."""
        sy = math.sqrt(m[0][0] ** 2 + m[1][0] ** 2)
        singular = sy < 1e-6

        if not singular:
            roll = math.atan2(m[2][1], m[2][2])
            pitch = math.atan2(-m[2][0], sy)
            yaw = math.atan2(m[1][0], m[0][0])
        else:
            roll = math.atan2(-m[1][2], m[1][1])
            pitch = math.atan2(-m[2][0], sy)
            yaw = 0.0

        return (roll, pitch, yaw)

    def generate(
        self,
        num_frames: int = 100,
        smoothness: float = 0.1,
        max_angle: float = 1.5708,
        output_type: str = "quaternion",
    ) -> list[tuple[float, float, float, float] | list[list[float]] | tuple[float, float, float]]:
        """
        Generate a camera rotation trajectory.

        Args:
            num_frames: Number of frames in trajectory.
            smoothness: Smoothness factor (0.0-1.0).
            max_angle: Maximum rotation per frame in radians.
            output_type: Output format - "quaternion", "matrix", or "euler".

        Returns:
            List of rotation values in specified format.
        """
        if not 0.0 <= smoothness <= 1.0:
            raise ValueError("smoothness must be between 0.0 and 1.0")
        if max_angle <= 0:
            raise ValueError("max_angle must be positive")
        if num_frames <= 0:
            raise ValueError("num_frames must be positive")

        quat = (1.0, 0.0, 0.0, 0.0)  # Identity quaternion
        trajectory = []

        for _ in range(num_frames):
            trajectory.append(quat)
            # Generate small random rotation
            delta = random.uniform(-max_angle * smoothness, max_angle * smoothness)
            axis = self._random_unit_vector()
            delta_q = self._axis_angle_to_quaternion(axis, delta)
            quat = self._normalize_quaternion(self._quaternion_multiply(quat, delta_q))

        if output_type == "matrix":
            return [self._quaternion_to_matrix(q) for q in trajectory]
        elif output_type == "euler":
            return [self._matrix_to_euler(self._quaternion_to_matrix(q)) for q in trajectory]
        elif output_type == "quaternion":
            return trajectory
        else:
            raise ValueError(f"Unknown output_type: {output_type}")

    def save(self, trajectory: list, path: Path, fmt: str = "json") -> None:
        """Save trajectory to file in JSON or CSV format."""
        path.parent.mkdir(parents=True, exist_ok=True)

        if fmt == "json":
            with open(path, "w") as f:
                json.dump(trajectory, f, indent=2)
        elif fmt == "csv":
            with open(path, "w") as f:
                # Write header
                if trajectory and isinstance(trajectory[0], tuple):
                    if len(trajectory[0]) == 4:  # quaternion
                        f.write("frame,w,x,y,z\n")
                    elif len(trajectory[0]) == 3:  # euler
                        f.write("frame,roll,pitch,yaw\n")
                elif trajectory and isinstance(trajectory[0], list):  # matrix
                    f.write("frame,m00,m01,m02,m10,m11,m12,m20,m21,m22\n")

                # Write data
                for i, item in enumerate(trajectory):
                    if isinstance(item, tuple):
                        vals = item
                    elif isinstance(item, list) and all(isinstance(row, list) for row in item):
                        # Flatten matrix
                        vals = [
                            item[0][0],
                            item[0][1],
                            item[0][2],
                            item[1][0],
                            item[1][1],
                            item[1][2],
                            item[2][0],
                            item[2][1],
                            item[2][2],
                        ]
                    else:
                        vals = item

                    f.write(f"{i}," + ",".join(map(str, vals)) + "\n")
        else:
            raise ValueError(f"Unknown format: {fmt}")


def main(argv: list[str] | None = None) -> int:
    """Main entry point for camera synthesizer CLI."""
    parser = argparse.ArgumentParser(
        description="Synthesize camera rotation trajectories for testing and simulation."
    )
    parser.add_argument(
        "-n",
        "--num-frames",
        type=int,
        default=100,
        help="Number of frames to generate (default: 100)",
    )
    parser.add_argument(
        "-s",
        "--smoothness",
        type=float,
        default=0.1,
        help="Smoothness factor 0.0-1.0 (default: 0.1)",
    )
    parser.add_argument(
        "-m",
        "--max-angle",
        type=float,
        default=1.5708,
        help="Max rotation per frame in radians (default: pi/2)",
    )
    parser.add_argument(
        "-t",
        "--output-type",
        choices=["quaternion", "matrix", "euler"],
        default="quaternion",
        help="Output format (default: quaternion)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output file path (default: auto-generated in temp directory)",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=["json", "csv"],
        default="json",
        help="Output file format (default: json)",
    )
    parser.add_argument("--seed", type=int, help="Random seed for reproducibility")

    args = parser.parse_args(argv)

    try:
        synthesizer = CameraRotationSynthesizer(seed=args.seed)
        trajectory = synthesizer.generate(
            num_frames=args.num_frames,
            smoothness=args.smoothness,
            max_angle=args.max_angle,
            output_type=args.output_type,
        )

        if args.output:
            output_path = args.output
        else:
            temp_dir = Path(tempfile.mkdtemp())
            output_path = temp_dir / f"camera_trajectory.{args.format}"

        synthesizer.save(trajectory, output_path, fmt=args.format)
        print(f"Generated {args.num_frames} frames -> {output_path}")
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
