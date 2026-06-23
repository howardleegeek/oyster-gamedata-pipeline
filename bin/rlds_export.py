#!/usr/bin/env python3
"""
G156 · bin/rlds_export.py

Cluster D: Convert one tarball into TFDS RLDS-format shard (tf.data.Dataset compatible).
Unlocks OXE pooling for robot learning datasets.

Usage: python bin/rlds_export.py --input data.tar.gz --output ./rlds_output --name my_dataset
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class RLDSStep:
    """Single step in an RLDS episode."""
    observation: Dict[str, Any] = field(default_factory=dict)
    action: List[float] = field(default_factory=list)
    reward: float = 0.0
    discount: float = 1.0
    is_first: bool = False
    is_last: bool = False
    is_terminal: bool = False


@dataclass
class RLDSConfig:
    """Configuration for RLDS dataset export."""
    dataset_name: str = "rlds_dataset"
    version: str = "1.0.0"
    description: str = "RLDS dataset exported from tarball"
    observation_space: Dict[str, Any] = field(default_factory=dict)
    action_space: Dict[str, Any] = field(default_factory=dict)


class TarballParser:
    """Parser for extracting RLDS data from tarball archives."""
    
    SUPPORTED_EXTENSIONS = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2")
    
    def __init__(self, tarball_path: Path, extract_dir: Path):
        self.tarball_path = tarball_path
        self.extract_dir = extract_dir
        self._extracted = False
    
    def validate_tarball(self) -> bool:
        """Validate that the tarball exists and is readable."""
        if not self.tarball_path.exists() or not self.tarball_path.is_file():
            logger.error(f"Tarball not found or not a file: {self.tarball_path}")
            return False
        if not self.tarball_path.name.endswith(self.SUPPORTED_EXTENSIONS):
            logger.warning(f"Unexpected file extension: {self.tarball_path.suffix}")
        return True
    
    def extract(self) -> Path:
        """Extract tarball contents to the extraction directory."""
        if not self.validate_tarball():
            raise ValueError(f"Invalid tarball: {self.tarball_path}")
        self.extract_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Extracting {self.tarball_path} to {self.extract_dir}")
        with tarfile.open(self.tarball_path, "r:*") as tar:
            members = [m for m in tar.getmembers()
                       if not m.name.startswith("/") and ".." not in m.name]
            tar.extractall(path=self.extract_dir, members=members)
        self._extracted = True
        return self.extract_dir
    
    def iter_episodes(self) -> Iterator[List[RLDSStep]]:
        """Iterate over episodes in the extracted data."""
        if not self._extracted:
            self.extract()
        for root, _, files in os.walk(self.extract_dir):
            for fname in files:
                fpath = Path(root) / fname
                try:
                    if fname.endswith(".json"):
                        steps = self._parse_json_episode(fpath)
                        if steps:
                            yield steps
                    elif fname.endswith((".npz", ".npy")):
                        steps = self._parse_numpy_episode(fpath)
                        if steps:
                            yield steps
                except Exception as e:
                    logger.warning(f"Failed to parse {fpath}: {e}")
    
    def _parse_json_episode(self, path: Path) -> List[RLDSStep]:
        """Parse an episode from a JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        steps = []
        episode_data = data.get("steps", [data]) if isinstance(data, dict) else data
        for i, step_data in enumerate(episode_data):
            steps.append(RLDSStep(
                observation=step_data.get("observation", {}),
                action=step_data.get("action", []),
                reward=float(step_data.get("reward", 0.0)),
                discount=float(step_data.get("discount", 1.0)),
                is_first=(i == 0),
                is_last=(i == len(episode_data) - 1),
                is_terminal=step_data.get("is_terminal", False),
            ))
        return steps
    
    def _parse_numpy_episode(self, path: Path) -> List[RLDSStep]:
        """Parse an episode from a numpy file."""
        import numpy as np
        data = np.load(str(path), allow_pickle=True)
        if path.suffix == ".npz":
            observations = data.get("observations", data.get("obs", []))
            actions = data.get("actions", data.get("action", []))
            rewards = data.get("rewards", data.get("reward", []))
        else:
            observations = data
            actions, rewards = [], []
        steps = []
        n_steps = len(observations) if hasattr(observations, "__len__") else 0
        for i in range(n_steps):
            obs = observations[i] if i < len(observations) else {}
            action = actions[i].tolist() if i < len(actions) else []
            reward = float(rewards[i]) if i < len(rewards) else 0.0
            steps.append(RLDSStep(
                observation={"array": obs} if not isinstance(obs, dict) else obs,
                action=action,
                reward=reward,
                is_first=(i == 0),
                is_last=(i == n_steps - 1),
            ))
        return steps


class RLDSWriter:
    """Writer for RLDS format shards."""
    
    def __init__(self, output_dir: Path, config: RLDSConfig):
        self.output_dir = output_dir
        self.config = config
        self._episode_count = 0
        self._step_count = 0
    
    def write_dataset_info(self) -> None:
        """Write dataset metadata in RLDS format."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        dataset_info = {
            "name": self.config.dataset_name,
            "version": self.config.version,
            "description": self.config.description,
            "observation_space": self.config.observation_space,
            "action_space": self.config.action_space,
            "episode_count": self._episode_count,
            "step_count": self._step_count,
        }
        with open(self.output_dir / "dataset_info.json", "w", encoding="utf-8") as f:
            json.dump(dataset_info, f, indent=2)
        logger.info(f"Wrote dataset info to {self.output_dir / 'dataset_info.json'}")
    
    def write_episode(self, steps: List[RLDSStep], episode_id: Optional[int] = None) -> None:
        """Write a single episode to RLDS format."""
        if episode_id is None:
            episode_id = self._episode_count
        self.output_dir.mkdir(parents=True, exist_ok=True)
        episode_path = self.output_dir / f"episode_{episode_id:06d}.jsonl"
        with open(episode_path, "w", encoding="utf-8") as f:
            for step in steps:
                step_dict = {
                    "observation": self._serialize(step.observation),
                    "action": step.action,
                    "reward": step.reward,
                    "discount": step.discount,
                    "is_first": step.is_first,
                    "is_last": step.is_last,
                    "is_terminal": step.is_terminal,
                }
                f.write(json.dumps(step_dict) + "\n")
        self._episode_count += 1
        self._step_count += len(steps)
        logger.debug(f"Wrote episode {episode_id} with {len(steps)} steps")
    
    def _serialize(self, data: Any) -> Any:
        """Serialize data for JSON compatibility."""
        if isinstance(data, dict):
            return {k: self._serialize(v) for k, v in data.items()}
        elif isinstance(data, (list, tuple)):
            return [self._serialize(v) for v in data]
        elif hasattr(data, "tolist"):
            return data.tolist()
        elif isinstance(data, (int, float, str, bool, type(None))):
            return data
        return str(data)
    
    def finalize(self) -> Dict[str, int]:
        """Finalize the RLDS dataset and return statistics."""
        self.write_dataset_info()
        features_spec = {
            "steps": {
                "observation": self.config.observation_space,
                "action": self.config.action_space,
                "reward": {"dtype": "float32", "shape": []},
                "discount": {"dtype": "float32", "shape": []},
                "is_first": {"dtype": "bool", "shape": []},
                "is_last": {"dtype": "bool", "shape": []},
                "is_terminal": {"dtype": "bool", "shape": []},
            }
        }
        with open(self.output_dir / "features.json", "w", encoding="utf-8") as f:
            json.dump(features_spec, f, indent=2)
        logger.info(f"Finalized RLDS dataset: {self._episode_count} episodes, {self._step_count} steps")
        return {"episode_count": self._episode_count, "step_count": self._step_count}


def export_tarball_to_rlds(
    input_path: Path,
    output_dir: Path,
    dataset_name: str,
    description: str = "",
) -> Dict[str, Any]:
    """Export a tarball to RLDS format."""
    with tempfile.TemporaryDirectory(prefix="rlds_export_") as tmp_dir:
        extract_dir = Path(tmp_dir) / "extracted"
        parser = TarballParser(input_path, extract_dir)
        config = RLDSConfig(
            dataset_name=dataset_name,
            description=description or f"RLDS dataset exported from {input_path.name}",
        )
        writer = RLDSWriter(output_dir, config)
        for episode_steps in parser.iter_episodes():
            writer.write_episode(episode_steps)
        stats = writer.finalize()
    stats["input_path"] = str(input_path)
    stats["output_dir"] = str(output_dir)
    return stats


def parse_args(argv: List[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Convert tarball to TFDS RLDS-format shard.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  %(prog)s --input data.tar.gz --output ./rlds_output\n  %(prog)s -i data.tar.gz -o ./output --name my_dataset",
    )
    parser.add_argument("-i", "--input", type=Path, required=True,
                        help="Input tarball path (supports .tar, .tar.gz, .tgz, .tar.bz2)")
    parser.add_argument("-o", "--output", type=Path, required=True,
                        help="Output directory for RLDS files")
    parser.add_argument("-n", "--name", type=str, default="rlds_dataset",
                        help="Dataset name (default: rlds_dataset)")
    parser.add_argument("-d", "--desc", type=str, default="", help="Dataset description")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--version", action="version", version="%(prog)s 1.0.0")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point for RLDS export."""
    if argv is None:
        argv = sys.argv[1:]
    args = parse_args(argv)
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    if not args.input.exists():
        logger.error(f"Input file not found: {args.input}")
        return 1
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        stats = export_tarball_to_rlds(
            input_path=args.input,
            output_dir=args.output,
            dataset_name=args.name,
            description=args.desc,
        )
        logger.info(f"Export complete: {stats['episode_count']} episodes, {stats['step_count']} steps")
        logger.info(f"Output written to: {args.output}")
        return 0
    except ValueError as e:
        logger.error(f"Export failed: {e}")
        return 1
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
