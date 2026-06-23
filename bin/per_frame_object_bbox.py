#!/usr/bin/env python3
"""
G168 · bin/per_frame_object_bbox.py

Per-frame 2D + 3D bounding boxes for visible NPCs/items (CARLA / nuScenes parity).
Filters visible objects by occlusion/truncation thresholds; exports JSON/CSV/YAML.
"""

import argparse
import csv
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# -- lazy imports -----------------------------------------------------------


def _lazy_yaml():
    try:
        import yaml

        return yaml
    except ImportError:
        raise ImportError("PyYAML required: pip install pyyaml")


def _lazy_pil():
    try:
        from PIL import Image, ImageDraw

        return Image, ImageDraw
    except ImportError:
        raise ImportError("Pillow required: pip install pillow")


# -- data models ------------------------------------------------------------


@dataclass
class BBox2D:
    """2D bounding box in image pixel coordinates."""
    x: float
    y: float
    width: float
    height: float
    confidence: float = 1.0
    class_id: str = "unknown"
    track_id: Optional[str] = None
    occlusion: float = 0.0
    truncation: float = 0.0

    def is_visible(self, oc: float = 0.5, tr: float = 0.5) -> bool:
        return self.occlusion <= oc and self.truncation <= tr

    def to_dict(self) -> Dict[str, Any]:
        return dict(
            x=self.x,
            y=self.y,
            width=self.width,
            height=self.height,
            confidence=self.confidence,
            class_id=self.class_id,
            track_id=self.track_id,
            occlusion=self.occlusion,
            truncation=self.truncation,
        )

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BBox2D":
        return cls(
            x=float(d.get("x", 0)),
            y=float(d.get("y", 0)),
            width=float(d.get("width", 0)),
            height=float(d.get("height", 0)),
            confidence=float(d.get("confidence", 1.0)),
            class_id=str(d.get("class_id", d.get("class", "unknown"))),
            track_id=d.get("track_id"),
            occlusion=float(d.get("occlusion", 0.0)),
            truncation=float(d.get("truncation", 0.0)),
        )


@dataclass
class BBox3D:
    """3D bounding box in world / ego coordinates."""
    x: float
    y: float
    z: float
    length: float
    width: float
    height: float
    yaw: float
    confidence: float = 1.0
    class_id: str = "unknown"
    track_id: Optional[str] = None

    def to_carla_dict(self) -> Dict[str, Any]:
        return {
            "location": {"x": self.x, "y": self.y, "z": self.z},
            "extent": {
                "x": self.length / 2,
                "y": self.width / 2,
                "z": self.height / 2,
            },
            "rotation": {
                "yaw": math.degrees(self.yaw),
                "pitch": 0.0,
                "roll": 0.0,
            },
            "class": self.class_id,
            "track_id": self.track_id,
            "confidence": self.confidence,
        }

    def to_nuscenes_dict(self) -> Dict[str, Any]:
        h = self.yaw / 2.0
        return {
            "translation": [self.x, self.y, self.z],
            "size": {"length": self.length, "width": self.width, "height": self.height},
            "rotation": {
                "qw": math.cos(h),
                "qx": 0.0,
                "qy": 0.0,
                "qz": math.sin(h),
            },
            "name": self.class_id,
            "token": self.track_id,
        }


@dataclass
class FrameDetections:
    """Container for per-frame detections."""
    frame_index: int
    timestamp: float
    bboxes_2d: List[BBox2D] = field(default_factory=list)
    bboxes_3d: List[BBox3D] = field(default_factory=list)
    camera_extrinsics: Optional[Dict[str, Any]] = None
    camera_intrinsics: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "frame_index": self.frame_index,
            "timestamp": self.timestamp,
            "bboxes_2d": [b.to_dict() for b in self.bboxes_2d],
            "bboxes_3d": [b.to_carla_dict() for b in self.bboxes_3d],
            "camera_extrinsics": self.camera_extrinsics,
            "camera_intrinsics": self.camera_intrinsics,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FrameDetections":
        return cls(
            frame_index=int(d["frame_index"]),
            timestamp=float(d["timestamp"]),
            bboxes_2d=[BBox2D.from_dict(b) for b in d.get("bboxes_2d", [])],
            bboxes_3d=[BBox3D(**b) for b in d.get("bboxes_3d", [])],
            camera_extrinsics=d.get("camera_extrinsics"),
            camera_intrinsics=d.get("camera_intrinsics"),
        )

    def filter_visible(
        self, occlusion_thresh: float = 0.5, truncation_thresh: float = 0.5
    ) -> "FrameDetections":
        """Return a new FrameDetections with only visible objects."""
        filtered_2d = [b for b in self.bboxes_2d if b.is_visible(occlusion_thresh, truncation_thresh)]
        return FrameDetections(
            frame_index=self.frame_index,
            timestamp=self.timestamp,
            bboxes_2d=filtered_2d,
            bboxes_3d=self.bboxes_3d,
            camera_extrinsics=self.camera_extrinsics,
            camera_intrinsics=self.camera_intrinsics,
        )


# -- core logic -------------------------------------------------------------


def parse_args():
    parser = argparse.ArgumentParser(
        description="G168: Per-frame 2D/3D bounding box extraction"
    )
    parser.add_argument("input", type=Path, help="Input video or directory")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("bboxes.json"),
        help="Output JSON/CSV/YAML path",
    )
    parser.add_argument(
        "--format",
        choices=["json", "csv", "yaml"],
        default="json",
        help="Output format",
    )
    parser.add_argument(
        "--occlusion-thresh",
        type=float,
        default=0.5,
        help="Max occlusion ratio to consider visible (0-1)",
    )
    parser.add_argument(
        "--truncation-thresh",
        type=float,
        default=0.5,
        help="Max truncation ratio to consider visible (0-1)",
    )
    parser.add_argument(
        "--export-3d",
        action="store_true",
        help="Export 3D boxes in CARLA format (default: nuScenes)",
    )
    parser.add_argument(
        "--render",
        type=Path,
        help="Render boxes onto frames and save to directory",
    )
    return parser.parse_args()


def extract_frames(video_path: Path) -> List[Any]:
    """Extract frames from video file using OpenCV."""
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    frames = []
    idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frames.append((idx, frame))
        idx += 1
    cap.release()
    return frames


def detect_objects(frame):
    """
    Placeholder: integrate yolo/centernet/etc here.
    Returns list of (bbox2d, bbox3d) tuples.
    """
    # TODO: integrate detection model
    return []


def project_3d_to_2d(bbox3d: BBox3D, intrinsics, extrinsics) -> Optional[BBox2D]:
    """Project 3D bbox center onto image plane using pinhole model."""
    import numpy as np

    # 3D center in camera frame
    cam_T = np.array(extrinsics.get("translation", [0, 0, 0]))
    cam_R = np.array(extrinsics.get("rotation", np.eye(3)))

    # world -> camera
    world_T = np.array([bbox3d.x, bbox3d.y, bbox3d.z])
    cam_point = cam_R @ (world_T - cam_T)

    if cam_point[2] <= 0:
        return None  # behind camera

    # camera -> image
    K = np.array(intrinsics.get("K", [[1, 0, 0], [0, 1, 0], [0, 0, 1]]))
    img = K @ cam_point
    u, v = img[0] / img[2], img[1] / img[2]

    # simple size estimate from depth
    scale = 1.0 / cam_point[2]
    w = bbox3d.width * scale * intrinsics.get("fx", 1)
    h = bbox3d.height * scale * intrinsics.get("fy", 1)

    return BBox2D(x=u - w / 2, y=v - h / 2, width=w, height=h)


def export_csv(frames: List[FrameDetections], out_path: Path):
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "frame",
                "timestamp",
                "type",
                "x",
                "y",
                "w",
                "h",
                "conf",
                "class",
                "track_id",
                "occlusion",
                "truncation",
            ]
        )
        for fd in frames:
            for b in fd.bboxes_2d:
                w.writerow(
                    [
                        fd.frame_index,
                        fd.timestamp,
                        "2d",
                        b.x,
                        b.y,
                        b.width,
                        b.height,
                        b.confidence,
                        b.class_id,
                        b.track_id,
                        b.occlusion,
                        b.truncation,
                    ]
                )


def render_boxes(frames_dir: Path, frames: List[FrameDetections]):
    """Draw 2D boxes onto frames and save."""
    Image, ImageDraw = _lazy_pil()

    for fd in frames:
        img_path = frames_dir / f"{fd.frame_index:06d}.png"
        if not img_path.exists():
            continue
        img = Image.open(img_path)
        draw = ImageDraw.Draw(img)
        for b in fd.bboxes_2d:
            draw.rectangle(
                [b.x, b.y, b.x + b.width, b.y + b.height],
                outline="red",
                width=2,
            )
        img.save(img_path)


def main():
    args = parse_args()
    input_path = args.input

    if input_path.is_dir():
        frame_files = sorted(input_path.glob("*.png")) + sorted(
            input_path.glob("*.jpg")
        )
        frames = [(i, None) for i, _ in enumerate(frame_files)]
    else:
        frames = extract_frames(input_path)

    detections = []
    for idx, frame in frames:
        # Placeholder: use real detection model
        dets = detect_objects(frame)
        fd = FrameDetections(
            frame_index=idx,
            timestamp=idx * (1.0 / 30.0),
            bboxes_2d=[d[0] for d in dets],
            bboxes_3d=[d[1] for d in dets],
        )
        fd = fd.filter_visible(args.occlusion_thresh, args.truncation_thresh)
        detections.append(fd)

    # Export
    if args.format == "json":
        with args.output.open("w") as f:
            json.dump([d.to_dict() for d in detections], f, indent=2)
    elif args.format == "yaml":
        yaml = _lazy_yaml()
        with args.output.open("w") as f:
            yaml.dump([d.to_dict() for d in detections], f)
    elif args.format == "csv":
        export_csv(detections, args.output)

    # Render
    if args.render:
        render_boxes(args.render, detections)


if __name__ == "__main__":
    main()
