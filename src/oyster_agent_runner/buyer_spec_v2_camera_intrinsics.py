#!/usr/bin/env python3
"""
Camera Intrinsics Specification V2 for Oyster Agent Runner.

Cluster A: per-frame K matrix + distortion + T_world_cam SE3 pose
(Ego-Exo4D / nuScenes / DROID standard).
Without proper camera intrinsics, depth EXR files are uninterpretable in 3D.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Lazy imports for optional dependencies
try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    np = None  # type: ignore


class CameraIntrinsicsSpec:
    """Validate camera intrinsics (K matrix, distortion) and extrinsics (SE3 pose)."""

    def __init__(
        self,
        K: list[list[float]] | None = None,
        D: list[float] | None = None,
        image_size: tuple[int, int] | None = None,
        camera_name: str = "",
        frame_id: str = "",
        T_world_cam: list[list[float]] | None = None,
    ):
        """
        Initialize camera intrinsics specification.

        Args:
            K: 3x3 intrinsic matrix [[fx, s, cx], [0, fy, cy], [0, 0, 1]]
            D: Distortion coefficients [k1, k2, p1, p2, k3, ...]
            image_size: (width, height) tuple in pixels
            camera_name: Camera identifier string
            frame_id: Frame/timestamp identifier
            T_world_cam: 4x4 SE3 transformation matrix (world to camera)
        """
        self.K = K
        self.D = D or []
        self.image_size = image_size
        self.camera_name = camera_name
        self.frame_id = frame_id
        self.T_world_cam = T_world_cam

    def validate(self) -> tuple[bool, list[str]]:
        """Validate all camera parameters. Returns (is_valid, list of errors)."""
        errors: list[str] = []
        if self.K is not None:
            _, k_err = self._validate_K()
            errors.extend(k_err)
        if self.D:
            _, d_err = self._validate_D()
            errors.extend(d_err)
        if self.T_world_cam is not None:
            _, t_err = self._validate_T()
            errors.extend(t_err)
        return len(errors) == 0, errors

    def _validate_K(self) -> tuple[bool, list[str]]:
        """Validate intrinsic matrix K."""
        errors: list[str] = []
        if not HAS_NUMPY:
            return False, ["NumPy required for K validation"]
        try:
            k = np.array(self.K, dtype=np.float64)  # type: ignore
            if k.shape != (3, 3):
                errors.append(f"K must be 3x3, got shape {k.shape}")
            if not np.allclose(k[2, 2], 1.0, atol=1e-6):  # type: ignore
                errors.append(f"K[2,2] should be 1.0, got {k[2, 2]}")
            if k[0, 0] <= 0 or k[1, 1] <= 0:  # type: ignore
                errors.append(f"Focal lengths must be positive: fx={k[0,0]}, fy={k[1,1]}")
            if self.image_size:
                w, h = self.image_size
                if not (0 < k[0, 2] < w):  # type: ignore
                    errors.append(f"Principal point cx={k[0,2]} outside image width {w}")
                if not (0 < k[1, 2] < h):  # type: ignore
                    errors.append(f"Principal point cy={k[1,2]} outside image height {h}")
        except (ValueError, TypeError) as e:
            errors.append(f"Invalid K matrix: {e}")
        return len(errors) == 0, errors

    def _validate_D(self) -> tuple[bool, list[str]]:
        """Validate distortion coefficients D."""
        errors: list[str] = []
        if not isinstance(self.D, (list, tuple)):
            return False, [f"D must be list, got {type(self.D).__name__}"]
        if len(self.D) < 4:
            errors.append(f"D should have at least 4 coeffs [k1,k2,p1,p2], got {len(self.D)}")
        for i, d in enumerate(self.D):
            if not isinstance(d, (int, float)):
                errors.append(f"D[{i}] is not numeric: {type(d).__name__}")
        return len(errors) == 0, errors

    def _validate_T(self) -> tuple[bool, list[str]]:
        """Validate SE3 transformation matrix T_world_cam."""
        errors: list[str] = []
        if not HAS_NUMPY:
            return False, ["NumPy required for T validation"]
        try:
            t = np.array(self.T_world_cam, dtype=np.float64)  # type: ignore
            if t.shape != (4, 4):
                errors.append(f"T_world_cam must be 4x4, got shape {t.shape}")
                return False, errors
            if not np.allclose(t[3, :], [0, 0, 0, 1], atol=1e-6):  # type: ignore
                errors.append(f"T_world_cam bottom row should be [0,0,0,1], got {t[3,:]}")
            r = t[:3, :3]  # type: ignore
            det = np.linalg.det(r)  # type: ignore
            if not np.allclose(abs(det), 1.0, atol=1e-3):  # type: ignore
                errors.append(f"Rotation determinant should be ±1, got {det}")
            if not np.allclose(r.T @ r, np.eye(3), atol=1e-3):  # type: ignore
                errors.append("Rotation matrix not orthogonal")
        except (ValueError, TypeError) as e:
            errors.append(f"Invalid T_world_cam matrix: {e}")
        return len(errors) == 0, errors

    def to_dict(self) -> dict[str, Any]:
        """Export to dictionary format."""
        result: dict[str, Any] = {"camera_name": self.camera_name, "frame_id": self.frame_id}
        if self.K is not None:
            result["K"] = self.K
        if self.D:
            result["D"] = self.D
        if self.image_size:
            result["image_size"] = list(self.image_size)
        if self.T_world_cam is not None:
            result["T_world_cam"] = self.T_world_cam
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CameraIntrinsicsSpec":
        """Create instance from dictionary (supports multiple naming conventions)."""
        K = data.get("K") or data.get("intrinsics_matrix")
        D = data.get("D") or data.get("distortion_coeffs") or data.get("distortion")
        T = data.get("T_world_cam") or data.get("extrinsics_matrix") or data.get("T")
        w = data.get("width") or data.get("image_width")
        h = data.get("height") or data.get("image_height")
        image_size = (w, h) if w and h else data.get("image_size")
        return cls(
            K=K,
            D=D,
            image_size=image_size,
            camera_name=data.get("camera_name", ""),
            frame_id=data.get("frame_id", ""),
            T_world_cam=T,
        )


def validate_file(path: Path, verbose: bool = False) -> int:
    """Validate a camera intrinsics JSON file. Returns 0 if valid, 1 if invalid, 2 on error."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {path}: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print(f"Error: File not found: {path}", file=sys.stderr)
        return 2

    spec = CameraIntrinsicsSpec.from_dict(data)
    is_valid, errors = spec.validate()

    if verbose:
        print(f"Camera: {spec.camera_name or 'unnamed'}")
        print(f"Frame: {spec.frame_id or 'unspecified'}")
        if spec.K:
            print(f"K matrix: fx={spec.K[0][0]:.2f}, fy={spec.K[1][1]:.2f}")
        if spec.D:
            print(f"Distortion: {len(spec.D)} coefficients")
        if spec.T_world_cam:
            print("Extrinsics: 4x4 SE3 matrix present")

    if is_valid:
        print(f"✓ Valid: {path}")
        return 0
    print(f"✗ Invalid: {path}", file=sys.stderr)
    for err in errors:
        print(f"  - {err}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    """Main entry point with argparse CLI. Returns exit code."""
    parser = argparse.ArgumentParser(description="Validate camera intrinsics specification files")
    parser.add_argument("files", nargs="+", type=Path, help="JSON files to validate")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print detailed info")
    parser.add_argument("-o", "--output", type=Path, help="Output validated specs to JSON file")
    args = parser.parse_args(argv)

    all_valid = True
    specs_data: list[dict[str, Any]] = []

    for filepath in args.files:
        result = validate_file(filepath, verbose=args.verbose)
        if result != 0:
            all_valid = False
        if args.output:
            try:
                with open(filepath, encoding="utf-8") as f:
                    data = json.load(f)
                spec = CameraIntrinsicsSpec.from_dict(data)
                specs_data.append(spec.to_dict())
            except (OSError, ValueError) as exc:
                print(
                    f"[WARN] skipping {filepath} for --output: {exc}",
                    file=sys.stderr,
                )

    if args.output and specs_data:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(specs_data, f, indent=2)
        print(f"Wrote {len(specs_data)} specs to {args.output}")

    return 0 if all_valid else 1


if __name__ == "__main__":
    sys.exit(main())
