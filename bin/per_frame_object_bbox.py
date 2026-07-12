#!/usr/bin/env python3
"""
G168 · bin/per_frame_object_bbox.py

Per-frame 2D + 3D bounding boxes for visible NPCs/items (CARLA / nuScenes parity).
Filters visible objects by occlusion/truncation thresholds; exports JSON/CSV/YAML.
"""

import argparse
import csv
import io
import json
import math
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# -- lazy imports -----------------------------------------------------------

def _lazy_yaml():
    try:
        import yaml
        return yaml
    except ImportError:
        raise ImportError("PyYAML required: pip install pyyaml") from None


def _lazy_pil():
    try:
        from PIL import Image, ImageDraw
        return Image, ImageDraw
    except ImportError:
        raise ImportError("Pillow required: pip install pillow") from None


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
        return dict(x=self.x, y=self.y, width=self.width, height=self.height,
                    confidence=self.confidence, class_id=self.class_id,
                    track_id=self.track_id, occlusion=self.occlusion,
                    truncation=self.truncation)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BBox2D":
        return cls(x=float(d.get("x", 0)), y=float(d.get("y", 0)),
                   width=float(d.get("width", 0)), height=float(d.get("height", 0)),
                   confidence=float(d.get("confidence", 1.0)),
                   class_id=str(d.get("class_id", d.get("class", "unknown"))),
                   track_id=d.get("track_id"),
                   occlusion=float(d.get("occlusion", 0.0)),
                   truncation=float(d.get("truncation", 0.0)))


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
        return {"location": {"x": self.x, "y": self.y, "z": self.z},
                "extent": {"x": self.length/2, "y": self.width/2, "z": self.height/2},
                "rotation": {"yaw": math.degrees(self.yaw), "pitch": 0.0, "roll": 0.0},
                "class": self.class_id, "track_id": self.track_id,
                "confidence": self.confidence}

    def to_nuscenes_dict(self) -> Dict[str, Any]:
        h = self.yaw / 2.0
        return {"translation": [self.x, self.y, self.z],
                "size": [self.length, self.width, self.height],
                "rotation": [math.cos(h), 0.0, 0.0, math.sin(h)],
                "detection_name": self.class_id, "track_id": self.track_id,
                "confidence": self.confidence}

    def to_dict(self) -> Dict[str, Any]:
        return dict(x=self.x, y=self.y, z=self.z, length=self.length,
                    width=self.width, height=self.height, yaw=self.yaw,
                    confidence=self.confidence, class_id=self.class_id,
                    track_id=self.track_id)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BBox3D":
        return cls(x=float(d.get("x", d.get("cx", 0))),
                   y=float(d.get("y", d.get("cy", 0))),
                   z=float(d.get("z", d.get("cz", 0))),
                   length=float(d.get("length", d.get("l", d.get("dx", 0)))),
                   width=float(d.get("width", d.get("w", d.get("dy", 0)))),
                   height=float(d.get("height", d.get("h", d.get("dz", 0)))),
                   yaw=float(d.get("yaw", d.get("rotation_z", d.get("heading", 0)))),
                   confidence=float(d.get("confidence", 1.0)),
                   class_id=str(d.get("class_id", d.get("class",
                                        d.get("detection_name", "unknown")))),
                   track_id=d.get("track_id"))


@dataclass
class FrameData:
    """Bounding boxes for a single frame."""
    frame_id: str
    timestamp: float
    bboxes_2d: List[BBox2D] = field(default_factory=list)
    bboxes_3d: List[BBox3D] = field(default_factory=list)
    camera_name: Optional[str] = None
    scene_id: Optional[str] = None

    def get_visible_2d(self, oc: float = 0.5, tr: float = 0.5) -> List[BBox2D]:
        return [b for b in self.bboxes_2d if b.is_visible(oc, tr)]

    def get_visible_3d(self, oc: float = 0.5, tr: float = 0.5) -> List[BBox3D]:
        ids = {b.track_id for b in self.get_visible_2d(oc, tr) if b.track_id}
        if not ids:
            return self.bboxes_3d
        return [b for b in self.bboxes_3d if b.track_id is None or b.track_id in ids]

    def to_dict(self, oc: float = 0.5, tr: float = 0.5) -> Dict[str, Any]:
        return {"frame_id": self.frame_id, "timestamp": self.timestamp,
                "camera_name": self.camera_name, "scene_id": self.scene_id,
                "bboxes_2d": [b.to_dict() for b in self.get_visible_2d(oc, tr)],
                "bboxes_3d": [b.to_dict() for b in self.get_visible_3d(oc, tr)]}


# -- parsers ----------------------------------------------------------------

def _parse_frame(raw: Dict[str, Any], fmt: str) -> FrameData:
    """Parse a single frame entry, adapting to CARLA / nuScenes / generic."""
    fid = str(raw.get("frame_id", raw.get("frame", raw.get("token", "0"))))
    ts = float(raw.get("timestamp", 0.0))
    if fmt == "nuscenes":
        ts /= 1e6  # nuScenes uses microseconds
    cam = raw.get("camera_name", raw.get("sensor", raw.get("channel")))
    scene = raw.get("scene_id", raw.get("scene_token"))
    b2 = [BBox2D.from_dict(o) for o in raw.get("objects_2d", raw.get("bboxes_2d", []))]
    b3 = [BBox3D.from_dict(o) for o in raw.get("objects_3d", raw.get("bboxes_3d", []))]
    return FrameData(frame_id=fid, timestamp=ts, bboxes_2d=b2, bboxes_3d=b3,
                     camera_name=cam, scene_id=scene)


def load_frames(path: Path, fmt: str) -> List[FrameData]:
    """Load frame data from a JSON file."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        # Check if dict is a single frame (has frame_id or bboxes)
        if any(k in data for k in ("frame_id", "bboxes_2d", "bboxes_3d", "objects_2d", "objects_3d")):
            frames_raw = [data]  # Single frame object
        else:
            frames_raw = (data.get("frames") or data.get("data") or
                          data.get("results") or data.get("annotations") or [])
            if isinstance(frames_raw, dict):
                frames_raw = [frames_raw]
    return [_parse_frame(fr, fmt) for fr in frames_raw]


# -- exporters --------------------------------------------------------------

def export_json(frames: List[FrameData], oc: float, tr: float) -> str:
    return json.dumps({"metadata": {"total_frames": len(frames),
                                     "occlusion_threshold": oc,
                                     "truncation_threshold": tr},
                       "frames": [f.to_dict(oc, tr) for f in frames]},
                      indent=2, ensure_ascii=False)


def export_csv(frames: List[FrameData], oc: float, tr: float) -> str:
    buf = io.StringIO()
    cols = ["frame_id","timestamp","camera_name","scene_id","bbox_type",
            "class_id","track_id","confidence","x","y","width","height",
            "x_3d","y_3d","z_3d","length","width_3d","height_3d","yaw",
            "occlusion","truncation"]
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for fr in frames:
        v2 = fr.get_visible_2d(oc, tr)
        v3 = fr.get_visible_3d(oc, tr)
        m3 = {b.track_id: b for b in v3 if b.track_id}
        for b2 in v2:
            b3 = m3.get(b2.track_id) if b2.track_id else None
            w.writerow({"frame_id": fr.frame_id, "timestamp": fr.timestamp,
                        "camera_name": fr.camera_name or "", "scene_id": fr.scene_id or "",
                        "bbox_type": "2d", "class_id": b2.class_id,
                        "track_id": b2.track_id or "", "confidence": b2.confidence,
                        "x": b2.x, "y": b2.y, "width": b2.width, "height": b2.height,
                        "x_3d": b3.x if b3 else "", "y_3d": b3.y if b3 else "",
                        "z_3d": b3.z if b3 else "", "length": b3.length if b3 else "",
                        "width_3d": b3.width if b3 else "",
                        "height_3d": b3.height if b3 else "",
                        "yaw": b3.yaw if b3 else "",
                        "occlusion": b2.occlusion, "truncation": b2.truncation})
        matched = {b2.track_id for b2 in v2 if b2.track_id}
        for b3 in v3:
            if b3.track_id and b3.track_id not in matched:
                w.writerow({"frame_id": fr.frame_id, "timestamp": fr.timestamp,
                            "camera_name": fr.camera_name or "",
                            "scene_id": fr.scene_id or "", "bbox_type": "3d",
                            "class_id": b3.class_id, "track_id": b3.track_id or "",
                            "confidence": b3.confidence,
                            "x": "", "y": "", "width": "", "height": "",
                            "x_3d": b3.x, "y_3d": b3.y, "z_3d": b3.z,
                            "length": b3.length, "width_3d": b3.width,
                            "height_3d": b3.height, "yaw": b3.yaw,
                            "occlusion": "", "truncation": ""})
    return buf.getvalue()


def export_yaml(frames: List[FrameData], oc: float, tr: float) -> str:
    yaml = _lazy_yaml()
    return yaml.dump({"metadata": {"total_frames": len(frames),
                                    "occlusion_threshold": oc,
                                    "truncation_threshold": tr},
                      "frames": [f.to_dict(oc, tr) for f in frames]},
                     default_flow_style=False, allow_unicode=True)


# -- image overlay ----------------------------------------------------------

def draw_overlay(image_path: Path, frame: FrameData,
                 oc: float, tr: float, out: Path) -> Path:
    """Draw visible 2D bboxes on an image (requires Pillow)."""
    Image, ImageDraw = _lazy_pil()
    img = Image.open(str(image_path)).convert("RGBA")
    draw = ImageDraw.Draw(img)
    colors = {"vehicle": (255,0,0,128), "pedestrian": (0,255,0,128),
              "bicycle": (0,0,255,128), "unknown": (255,255,0,128)}
    for b in frame.get_visible_2d(oc, tr):
        x0, y0 = int(b.x), int(b.y)
        x1, y1 = x0+int(b.width), y0+int(b.height)
        c = colors.get(b.class_id.lower(), colors["unknown"])
        draw.rectangle([x0, y0, x1, y1], outline=c[:3], width=2)
        draw.text((x0, y0-14), f"{b.class_id} ({b.confidence:.2f})", fill=c[:3])
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out))
    return out


# -- CLI --------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Per-frame 2D + 3D bounding boxes for visible NPCs/items.")
    p.add_argument("--input", "-i", required=True, type=Path,
                   help="Input JSON file with frame bbox data.")
    p.add_argument("--output", "-o", type=Path, default=None,
                   help="Output file (default: stdout).")
    p.add_argument("--format", "-f", choices=["carla","nuscenes","generic"],
                   default="generic", help="Input format (default: generic).")
    p.add_argument("--output-format", choices=["json","csv","yaml"],
                   default="json", help="Output format (default: json).")
    p.add_argument("--occlusion-thresh", type=float, default=0.5,
                   help="Max occlusion for visibility (default: 0.5).")
    p.add_argument("--truncation-thresh", type=float, default=0.5,
                   help="Max truncation for visibility (default: 0.5).")
    p.add_argument("--image", type=Path, default=None,
                   help="Image to overlay 2D bboxes (requires Pillow).")
    p.add_argument("--image-output", type=Path, default=None,
                   help="Overlay output path.")
    p.add_argument("--stats", action="store_true",
                   help="Print summary stats to stderr.")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point. Returns 0 on success, non-zero on error."""
    args = build_parser().parse_args(argv)
    if not args.input.exists():
        print(f"Error: input not found: {args.input}", file=sys.stderr)
        return 1
    try:
        frames = load_frames(args.input, args.format)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"Error parsing input: {exc}", file=sys.stderr)
        return 1
    if not frames:
        print("Warning: no frames found.", file=sys.stderr)

    exporters = {"json": export_json, "csv": export_csv, "yaml": export_yaml}
    try:
        output_str = exporters[args.output_format](
            frames, args.occlusion_thresh, args.truncation_thresh)
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(output_str)
    else:
        sys.stdout.write(output_str + "\n")

    if args.image:
        if not args.image.exists():
            print(f"Error: image not found: {args.image}", file=sys.stderr)
            return 1
        img_out = args.image_output or (
            args.output.with_suffix(".png") if args.output
            else Path(tempfile.mkdtemp()) / "overlay.png")
        try:
            draw_overlay(args.image, frames[0], args.occlusion_thresh,
                         args.truncation_thresh, img_out)
            print(f"Overlay saved: {img_out}", file=sys.stderr)
        except ImportError as exc:
            print(f"Warning: overlay skipped — {exc}", file=sys.stderr)

    if args.stats:
        v2 = sum(len(f.get_visible_2d(args.occlusion_thresh,
                                       args.truncation_thresh)) for f in frames)
        v3 = sum(len(f.get_visible_3d(args.occlusion_thresh,
                                       args.truncation_thresh)) for f in frames)
        print(f"Frames: {len(frames)} | Visible 2D: {v2} | Visible 3D: {v3}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
