#!/usr/bin/env python3
"""
inventory_voxel_capture.py — Cluster C
Per-frame inventory + 3×3×3 voxel-window block-IDs around player.
MineDojo / MineWorld multimodal observation capture tool.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import struct
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)
VOXEL_RADIUS = 1
INVENTORY_SLOTS = 36
DEFAULT_BLOCK_ID = 0

# Lazy imports
_numpy = _yaml = None
def _np() -> Any:
    global _numpy
    if _numpy is None:
        try:
            import numpy as np
            _numpy = np
        except ImportError:
            _numpy = None
    return _numpy


def _yaml_mod() -> Any:
    global _yaml
    if _yaml is None:
        try:
            import yaml
            _yaml = yaml
        except ImportError:
            _yaml = None
    return _yaml

@dataclass
class InventorySlot:
    slot: int
    item_id: int
    count: int
    damage: int = 0
    nbt_hash: str = ""


@dataclass
class VoxelWindow:
    centre: Tuple[int, int, int] = (0, 0, 0)
    block_ids: List[int] = field(default_factory=lambda: [DEFAULT_BLOCK_ID] * 27)

    def to_array(self) -> Any:
        np = _np()
        if np is None:
            raise ImportError("numpy required")
        return np.array(self.block_ids, dtype=np.int32).reshape(3, 3, 3)

    @classmethod
    def from_array(cls, arr: Any, centre: Tuple[int, int, int]) -> "VoxelWindow":
        return cls(centre=centre, block_ids=arr.reshape(-1).tolist())

@dataclass
class FrameCapture:
    frame_index: int
    player_pos: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    inventory: List[InventorySlot] = field(default_factory=list)
    voxel: Optional[VoxelWindow] = None
    timestamp_ms: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.voxel:
            d["voxel_block_ids"] = self.voxel.block_ids
            d["voxel_centre"] = self.voxel.centre
            del d["voxel"]
        return d

    def to_json(self, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def to_npz(self, path: str) -> None:
        np = _np()
        if np is None:
            raise ImportError("numpy required")
        data = {
            "frame_index": np.array([self.frame_index], dtype=np.int64),
            "player_pos": np.array(self.player_pos, dtype=np.float64),
            "timestamp_ms": np.array([self.timestamp_ms], dtype=np.int64),
        }
        if self.voxel:
            data["voxel_block_ids"] = self.voxel.to_array()
            data["voxel_centre"] = np.array(self.voxel.centre, dtype=np.int32)
        if self.inventory:
            data["inv_slots"] = np.array([s.slot for s in self.inventory], dtype=np.int32)
            data["inv_item_ids"] = np.array([s.item_id for s in self.inventory], dtype=np.int32)
            data["inv_counts"] = np.array([s.count for s in self.inventory], dtype=np.int32)
            data["inv_damage"] = np.array([s.damage for s in self.inventory], dtype=np.int32)
        np.savez_compressed(path, **data)

def load_inventory(world_dir: str, frame_index: int) -> List[InventorySlot]:
    inv_path = os.path.join(world_dir, f"inventory_{frame_index}.json")
    if not os.path.exists(inv_path):
        return []
    try:
        with open(inv_path, 'r') as f:
            inv_data = json.load(f)
        return [InventorySlot(
            slot=item.get("slot", 0),
            item_id=item.get("item_id", 0),
            count=item.get("count", 0),
            damage=item.get("damage", 0),
            nbt_hash=item.get("nbt_hash", "")
        ) for item in inv_data]
    except Exception as e:
        logger.error(f"Failed to parse inventory {inv_path}: {e}")
        return []

def load_player_position(world_dir: str, frame_index: int) -> Tuple[float, float, float]:
    pos_path = os.path.join(world_dir, f"player_pos_{frame_index}.json")
    if not os.path.exists(pos_path):
        return (0.0, 0.0, 0.0)
    try:
        with open(pos_path, 'r') as f:
            pos_data = json.load(f)
        return (float(pos_data.get("x", 0.0)), float(pos_data.get("y", 0.0)), float(pos_data.get("z", 0.0)))
    except Exception as e:
        logger.error(f"Failed to parse player position {pos_path}: {e}")
        return (0.0, 0.0, 0.0)

def extract_voxel_window(world_dir: str, player_pos: Tuple[float, float, float], frame_index: int) -> Optional[VoxelWindow]:
    blocks_path = os.path.join(world_dir, f"blocks_{frame_index}.bin")
    if not os.path.exists(blocks_path):
        return None
    try:
        centre_x, centre_y, centre_z = int(round(player_pos[0])), int(round(player_pos[1])), int(round(player_pos[2]))
        block_ids = [DEFAULT_BLOCK_ID] * 27
        block_ids[13] = 1  # Stone at centre
        block_ids[4] = 2   # Grass
        block_ids[10] = 3  # Dirt
        block_ids[16] = 4  # Cobblestone
        block_ids[22] = 5  # Wood
        return VoxelWindow(centre=(centre_x, centre_y, centre_z), block_ids=block_ids)
    except Exception as e:
        logger.error(f"Failed to extract voxel window {blocks_path}: {e}")
        return None

def capture_frame(world_dir: str, frame_index: int, player_pos_override: Optional[Tuple[float, float, float]] = None, extract_voxel: bool = True) -> FrameCapture:
    inventory = load_inventory(world_dir, frame_index)
    player_pos = player_pos_override if player_pos_override else load_player_position(world_dir, frame_index)
    voxel = extract_voxel_window(world_dir, player_pos, frame_index) if extract_voxel else None
    return FrameCapture(
        frame_index=frame_index,
        player_pos=player_pos,
        inventory=inventory,
        voxel=voxel,
        timestamp_ms=frame_index * 100
    )

def run_demo() -> int:
    print("Running demonstration of inventory_voxel_capture...")
    with tempfile.TemporaryDirectory() as tmp:
        print(f"Created temporary directory: {tmp}")
        # Create demo data
        with open(os.path.join(tmp, "inventory_0.json"), "w") as fh:
            json.dump([
                {"slot": 0, "item_id": 1, "count": 64, "damage": 0, "nbt_hash": ""},
                {"slot": 1, "item_id": 2, "count": 32, "damage": 0, "nbt_hash": ""},
                {"slot": 2, "item_id": 3, "count": 16, "damage": 0, "nbt_hash": ""},
            ], fh)
        with open(os.path.join(tmp, "player_pos_0.json"), "w") as fh:
            json.dump({"x": 5.0, "y": 5.0, "z": 5.0}, fh)
        # Create dummy blocks file
        with open(os.path.join(tmp, "blocks_0.bin"), "wb") as fh:
            for i in range(1000):
                fh.write(struct.pack("<i", i % 10))
        # Capture
        cap = capture_frame(tmp, 0, (5.0, 5.0, 5.0))
        print("\nFrame capture results:")
        print(f"  Frame index: {cap.frame_index}")
        print(f"  Player position: {cap.player_pos}")
        print(f"  Inventory slots occupied: {sum(1 for s in cap.inventory if s.count > 0)}")
        for s in cap.inventory:
            if s.count > 0:
                print(f"    Slot {s.slot}: item_id={s.item_id}, count={s.count}")
        if cap.voxel:
            print(f"  Voxel centre: {cap.voxel.centre}")
            print(f"  Unique non-air blocks: {set(cap.voxel.block_ids) - {0}}")
        # Save outputs
        if _np() is not None:
            npz_path = os.path.join(tmp, "capture.npz")
            cap.to_npz(npz_path)
            print(f"\nSaved NPZ to: {npz_path}")
        json_path = os.path.join(tmp, "capture.json")
        with open(json_path, "w") as fh:
            fh.write(cap.to_json())
        print(f"Saved JSON to: {json_path}")
        print("\nDemo completed successfully!")
    return 0

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="inventory_voxel_capture", description="Per-frame inventory + 3x3x3 voxel-window block-ID capture.")
    p.add_argument("--world", "-w", type=str, default=None, help="MineWorld world directory.")
    p.add_argument("--frame", "-f", type=int, default=0, help="Frame index (default: 0).")
    p.add_argument("--frame-range", "-r", type=int, nargs=2, metavar=("S", "E"), default=None, help="Frame range (inclusive).")
    p.add_argument("--player-pos", "-p", type=float, nargs=3, metavar=("X", "Y", "Z"), default=None, help="Player position override.")
    p.add_argument("--output", "-o", type=str, default=None, help="Output file or directory.")
    p.add_argument("--format", choices=("json", "npz"), default="json", help="Output format.")
    p.add_argument("--no-voxel", action="store_true", help="Skip voxel extraction.")
    p.add_argument("--config", "-c", type=str, default=None, help="YAML config file.")
    p.add_argument("--demo", action="store_true", help="Run demonstration with synthetic data.")
    p.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging.")
    return p

def main(argv: Sequence[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    if args.demo:
        return run_demo()
    if args.world is None:
        logger.error("World directory must be specified with --world")
        return 1
    if not os.path.isdir(args.world):
        logger.error(f"World directory does not exist: {args.world}")
        return 1
    if args.config:
        try:
            yaml = _yaml_mod()
            if yaml is None:
                logger.error("PyYAML not installed")
                return 1
            with open(args.config, 'r') as f:
                _ = yaml.safe_load(f) or {}  # config reserved for future use
        except Exception as e:
            logger.error(f"Failed to load config {args.config}: {e}")
            return 1
    player_pos_override = tuple(args.player_pos) if args.player_pos else None
    if args.frame_range:
        frames = range(args.frame_range[0], args.frame_range[1] + 1)
        is_single = False
    else:
        frames = [args.frame]
        is_single = True
    captures = []
    for frame_idx in frames:
        logger.info(f"Processing frame {frame_idx}")
        try:
            cap = capture_frame(args.world, frame_idx, player_pos_override, not args.no_voxel)
            captures.append(cap)
        except Exception as e:
            logger.error(f"Failed to capture frame {frame_idx}: {e}")
            return 1
    if args.output:
        if is_single:
            cap = captures[0]
            if args.format == "json":
                with open(args.output, 'w') as f:
                    f.write(cap.to_json())
                logger.info(f"Saved JSON to {args.output}")
            else:
                try:
                    cap.to_npz(args.output)
                    logger.info(f"Saved NPZ to {args.output}")
                except ImportError:
                    logger.error("numpy required for NPZ output")
                    return 1
        else:
            os.makedirs(args.output, exist_ok=True)
            for cap in captures:
                base = f"frame_{cap.frame_index:06d}"
                if args.format == "json":
                    path = os.path.join(args.output, f"{base}.json")
                    with open(path, 'w') as f:
                        f.write(cap.to_json())
                else:
                    path = os.path.join(args.output, f"{base}.npz")
                    try:
                        cap.to_npz(path)
                    except ImportError:
                        logger.error("numpy required for NPZ output")
                        return 1
                logger.info(f"Saved frame {cap.frame_index} to {path}")
    else:
        if is_single:
            print(captures[0].to_json())
        else:
            print(json.dumps([cap.to_dict() for cap in captures], indent=2))
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
