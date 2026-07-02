#!/usr/bin/env python3
"""
bin/seg_track_provider.py

Cluster C: Per-pixel semantic + instance segmentation provider.
Encoding: R=class_id (Minecraft block IDs), G+B=instance_id (CARLA/Habitat convention).
Target: 6 FPS real-time processing.

Usage: seg_track_provider.py --input <path> --output <path> [options]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("seg_track_provider")

_numpy: Any = None
_pil_image: Any = None


def _get_numpy() -> Any:
    global _numpy
    if _numpy is None:
        import numpy as np
        _numpy = np
    return _numpy


def _get_pil_image() -> Any:
    global _pil_image
    if _pil_image is None:
        from PIL import Image
        _pil_image = Image
    return _pil_image


# Minecraft block IDs for semantic segmentation
BLOCK_IDS: Dict[str, int] = {
    "air": 0, "stone": 1, "grass": 2, "dirt": 3, "cobblestone": 4, "oak_planks": 5,
    "water": 9, "lava": 11, "sand": 12, "gravel": 13, "oak_log": 17, "leaves": 18,
    "glass": 20, "bedrock": 7, "brick": 45, "vehicle": 100, "pedestrian": 101,
    "traffic_light": 102, "traffic_sign": 103, "road": 104, "sidewalk": 105,
    "building": 106, "wall": 107, "fence": 108, "pole": 109, "sky": 110,
    "vegetation": 111, "terrain": 112, "rider": 113, "bicycle": 114,
    "motorcycle": 115, "bus": 116, "truck": 117, "train": 118, "unknown": 255,
}
ID_TO_BLOCK: Dict[int, str] = {v: k for k, v in BLOCK_IDS.items()}


@dataclass
class Config:
    """Configuration for segmentation provider."""
    input_path: str = ""
    output_path: str = ""
    target_fps: float = 6.0
    max_instances: int = 65535
    min_instance_area: int = 100
    connectivity: int = 8
    enable_tracking: bool = True
    tracking_max_age: int = 30
    format: str = "png"

    def validate(self) -> List[str]:
        errors = []
        if self.target_fps <= 0 or self.target_fps > 60:
            errors.append("target_fps must be 1-60")
        if not 1 <= self.max_instances <= 65535:
            errors.append("max_instances must be 1-65535")
        if self.connectivity not in (4, 8):
            errors.append("connectivity must be 4 or 8")
        if self.format not in ("png", "npy", "npz"):
            errors.append(f"Unsupported format: {self.format}")
        return errors


@dataclass
class InstanceInfo:
    """Information about a detected instance."""
    instance_id: int
    class_id: int
    class_name: str
    pixel_count: int
    centroid: Tuple[int, int] = (0, 0)
    bbox: Tuple[int, int, int, int] = (0, 0, 0, 0)
    track_id: int = -1
    confidence: float = 1.0


@dataclass
class Result:
    """Segmentation result."""
    frame_id: int
    timestamp: float
    mask: Any
    instances: List[InstanceInfo] = field(default_factory=list)
    processing_time_ms: float = 0.0
    fps: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "frame_id": self.frame_id, "timestamp": self.timestamp,
            "instance_count": len(self.instances),
            "instances": [{"id": i.instance_id, "class_id": i.class_id,
                          "class_name": i.class_name, "pixels": i.pixel_count,
                          "centroid": i.centroid, "bbox": i.bbox,
                          "track_id": i.track_id} for i in self.instances],
            "processing_time_ms": self.processing_time_ms, "fps": self.fps,
        }


class SegmentationProvider:
    """Per-pixel semantic + instance segmentation provider.
    
    Encoding: R=class_id, G=instance_id_high, B=instance_id_low
    Instance ID = (G << 8) | B (range 0-65535)
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self._frame_count = 0
        self._trackers: Dict[int, Dict[str, Any]] = {}
        self._next_track_id = 0
        errors = config.validate()
        if errors:
            raise ValueError(f"Invalid config: {'; '.join(errors)}")
        logger.info(f"Initialized SegmentationProvider, fps={config.target_fps}")

    def encode_mask(self, class_ids: Any, instance_ids: Any) -> Any:
        """Encode class and instance IDs into RGB mask."""
        np = _get_numpy()
        h, w = class_ids.shape
        mask = np.zeros((h, w, 3), dtype=np.uint8)
        mask[:, :, 0] = np.clip(class_ids, 0, 255).astype(np.uint8)
        mask[:, :, 1] = ((instance_ids >> 8) & 0xFF).astype(np.uint8)
        mask[:, :, 2] = (instance_ids & 0xFF).astype(np.uint8)
        return mask

    def decode_mask(self, mask: Any) -> Tuple[Any, Any]:
        """Decode RGB mask into class and instance IDs."""
        np = _get_numpy()
        class_ids = mask[:, :, 0].astype(np.int32)
        instance_ids = (mask[:, :, 1].astype(np.int32) << 8) | mask[:, :, 2].astype(np.int32)
        return class_ids, instance_ids

    def process_frame(self, frame: Any, frame_id: Optional[int] = None,
                      timestamp: Optional[float] = None) -> Result:
        """Process a single frame for segmentation."""
        np = _get_numpy()
        start_time = time.perf_counter()
        
        frame_id = frame_id if frame_id is not None else self._frame_count
        timestamp = timestamp if timestamp is not None else time.time()
        
        gray = np.mean(frame, axis=2).astype(np.uint8) if len(frame.shape) == 3 else frame
        class_ids = self._classify_pixels(gray)
        instance_ids, instances = self._detect_instances(class_ids)
        mask = self.encode_mask(class_ids, instance_ids)
        
        if self.config.enable_tracking:
            instances = self._track_instances(instances)
        
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        self._frame_count += 1
        
        return Result(frame_id=frame_id, timestamp=timestamp, mask=mask,
                      instances=instances, processing_time_ms=elapsed_ms,
                      fps=1000.0 / elapsed_ms if elapsed_ms > 0 else 0.0)

    def _classify_pixels(self, gray: Any) -> Any:
        """Classify pixels into semantic classes based on intensity."""
        np = _get_numpy()
        class_ids = np.full(gray.shape, BLOCK_IDS["unknown"], dtype=np.int32)
        class_ids[gray < 30] = BLOCK_IDS["sky"]
        class_ids[(gray >= 30) & (gray < 60)] = BLOCK_IDS["water"]
        class_ids[(gray >= 60) & (gray < 90)] = BLOCK_IDS["vegetation"]
        class_ids[(gray >= 90) & (gray < 120)] = BLOCK_IDS["grass"]
        class_ids[(gray >= 120) & (gray < 150)] = BLOCK_IDS["dirt"]
        class_ids[(gray >= 150) & (gray < 180)] = BLOCK_IDS["stone"]
        class_ids[(gray >= 180) & (gray < 210)] = BLOCK_IDS["sand"]
        class_ids[gray >= 210] = BLOCK_IDS["building"]
        return class_ids

    def _detect_instances(self, class_ids: Any) -> Tuple[Any, List[InstanceInfo]]:
        """Detect instances using connected components."""
        np = _get_numpy()
        h, w = class_ids.shape
        instance_ids = np.zeros((h, w), dtype=np.int32)
        instances: List[InstanceInfo] = []
        current_instance = 0
        
        for class_id in np.unique(class_ids):
            if class_id == 0:
                continue
            binary = (class_ids == class_id).astype(np.uint8)
            labeled, num_features = self._label_connected(binary)
            
            for label_id in range(1, num_features + 1):
                if current_instance >= self.config.max_instances:
                    break
                component = (labeled == label_id)
                pixel_count = int(np.sum(component))
                if pixel_count < self.config.min_instance_area:
                    continue
                
                current_instance += 1
                instance_ids[component] = current_instance
                y_coords, x_coords = np.where(component)
                centroid = (int(np.mean(x_coords)), int(np.mean(y_coords)))
                bbox = (int(np.min(x_coords)), int(np.min(y_coords)),
                        int(np.max(x_coords)) - int(np.min(x_coords)) + 1,
                        int(np.max(y_coords)) - int(np.min(y_coords)) + 1)
                instances.append(InstanceInfo(
                    instance_id=current_instance, class_id=int(class_id),
                    class_name=ID_TO_BLOCK.get(int(class_id), "unknown"),
                    pixel_count=pixel_count, centroid=centroid, bbox=bbox))
        
        return instance_ids, instances

    def _label_connected(self, binary: Any) -> Tuple[Any, int]:
        """Label connected components using flood fill."""
        np = _get_numpy()
        h, w = binary.shape
        labeled = np.zeros((h, w), dtype=np.int32)
        label_id = 0
        neighbors = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)] \
                    if self.config.connectivity == 8 else [(-1, 0), (0, -1), (0, 1), (1, 0)]
        
        for y in range(h):
            for x in range(w):
                if binary[y, x] == 1 and labeled[y, x] == 0:
                    label_id += 1
                    stack = [(y, x)]
                    while stack:
                        cy, cx = stack.pop()
                        if 0 <= cy < h and 0 <= cx < w and binary[cy, cx] == 1 and labeled[cy, cx] == 0:
                            labeled[cy, cx] = label_id
                            for dy, dx in neighbors:
                                stack.append((cy + dy, cx + dx))
        return labeled, label_id

    def _track_instances(self, instances: List[InstanceInfo]) -> List[InstanceInfo]:
        """Track instances across frames using centroid distance."""
        for inst in instances:
            best_match, best_dist = -1, float("inf")
            for tid, tracker in self._trackers.items():
                if tracker["age"] <= self.config.tracking_max_age:
                    tx, ty = tracker["centroid"]
                    dist = ((tx - inst.centroid[0]) ** 2 + (ty - inst.centroid[1]) ** 2) ** 0.5
                    if dist < best_dist and dist < 50:
                        best_match, best_dist = tid, dist
            if best_match >= 0:
                inst.track_id = best_match
                self._trackers[best_match]["centroid"] = inst.centroid
                self._trackers[best_match]["age"] = 0
            else:
                inst.track_id = self._next_track_id
                self._trackers[self._next_track_id] = {"centroid": inst.centroid, "age": 0}
                self._next_track_id += 1
        
        for tid in list(self._trackers.keys()):
            self._trackers[tid]["age"] += 1
            if self._trackers[tid]["age"] > self.config.tracking_max_age:
                del self._trackers[tid]
        return instances

    def save_result(self, result: Result, output_dir: str) -> Tuple[str, str]:
        """Save segmentation result to files."""
        np = _get_numpy()
        Image = _get_pil_image()
        os.makedirs(output_dir, exist_ok=True)
        
        mask_path = os.path.join(output_dir, f"seg_{result.frame_id:06d}.{self.config.format}")
        if self.config.format == "png":
            Image.fromarray(result.mask).save(mask_path)
        else:
            np.save(mask_path, result.mask)
        
        meta_path = os.path.join(output_dir, f"seg_{result.frame_id:06d}.json")
        with open(meta_path, "w") as f:
            json.dump(result.to_dict(), f, indent=2)
        return mask_path, meta_path

    def process_directory(self, input_dir: str, output_dir: str) -> Iterator[Result]:
        """Process all frames in a directory."""
        np = _get_numpy()
        Image = _get_pil_image()
        frame_interval = 1.0 / self.config.target_fps
        input_path = Path(input_dir)
        frame_files = sorted(input_path.glob("*.png")) + sorted(input_path.glob("*.jpg"))
        logger.info(f"Found {len(frame_files)} frames")
        
        for frame_file in frame_files:
            frame_start = time.time()
            frame = np.array(Image.open(frame_file))
            result = self.process_frame(frame)
            self.save_result(result, output_dir)
            yield result
            elapsed = time.time() - frame_start
            if elapsed < frame_interval:
                time.sleep(frame_interval - elapsed)


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point for segmentation provider."""
    parser = argparse.ArgumentParser(
        description="Per-pixel semantic + instance segmentation provider",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Encoding: R=class_id, G=instance_id_high, B=instance_id_low")
    parser.add_argument("--input", "-i", required=True, help="Input file or directory")
    parser.add_argument("--output", "-o", required=True, help="Output file or directory")
    parser.add_argument("--fps", type=float, default=6.0, help="Target FPS (default: 6)")
    parser.add_argument("--single", action="store_true", help="Process single image")
    parser.add_argument("--track", action="store_true", default=True, help="Enable tracking")
    parser.add_argument("--no-track", action="store_true", help="Disable tracking")
    parser.add_argument("--format", "-f", choices=["png", "npy", "npz"], default="png")
    parser.add_argument("--min-area", type=int, default=100, help="Min instance area")
    parser.add_argument("--max-instances", type=int, default=65535)
    parser.add_argument("--connectivity", type=int, choices=[4, 8], default=8)
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    
    args = parser.parse_args(argv)
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    config = Config(
        input_path=args.input, output_path=args.output, target_fps=args.fps,
        max_instances=args.max_instances, min_instance_area=args.min_area,
        connectivity=args.connectivity, enable_tracking=args.track and not args.no_track,
        format=args.format)
    
    errors = config.validate()
    if errors:
        logger.error(f"Config errors: {'; '.join(errors)}")
        return 1
    
    try:
        provider = SegmentationProvider(config)
    except ValueError as e:
        logger.error(f"Init failed: {e}")
        return 1
    
    np = _get_numpy()
    Image = _get_pil_image()
    
    if args.single:
        logger.info(f"Processing single image: {args.input}")
        frame = np.array(Image.open(args.input))
        result = provider.process_frame(frame)
        output_path = args.output if args.output.endswith(f".{args.format}") else f"{args.output}.{args.format}"
        if args.format == "png":
            Image.fromarray(result.mask).save(output_path)
        else:
            np.save(output_path, result.mask)
        print(f"Processed in {result.processing_time_ms:.2f}ms, {len(result.instances)} instances")
        print(f"Output: {output_path}")
    else:
        logger.info(f"Processing directory: {args.input}")
        frame_count, total_time = 0, 0.0
        for result in provider.process_directory(args.input, args.output):
            frame_count += 1
            total_time += result.processing_time_ms
            if frame_count % 10 == 0:
                logger.info(f"Processed {frame_count} frames")
        avg_fps = 1000.0 / (total_time / frame_count) if frame_count > 0 else 0
        logger.info(f"Done: {frame_count} frames, {avg_fps:.1f} fps")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
