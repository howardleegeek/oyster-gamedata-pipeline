#!/usr/bin/env python3
"""bin/lerobot_export.py — Convert a tarball of robot episodes to LeRobot HuggingFace format.

Cluster D: HF-hub distribution path for robotics buyers.
Reads a tarball containing episode data (JSON/JSONL/CSV) and produces a
LeRobot-compatible dataset directory with meta/ and data/ subdirectories.

Usage:
    python bin/lerobot_export.py input.tar.gz -o ./lerobot_dataset
    python bin/lerobot_export.py input.tar.gz -o ./ds --repo-id org/name --push
"""
from __future__ import annotations

import argparse, csv, json, logging, os, sys, tarfile, tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# -- lazy imports for heavy optional deps -----------------------------------

def _numpy() -> Any:
    """Lazily import numpy, returning the module.

    This is a lazy import to avoid heavy dependency at module load time.

    Returns:
        The numpy module.

    Raises:
        ImportError: If numpy is not installed.
    """
    import numpy as np  # noqa: F401
    return np

def _pil_image():
    from PIL import Image; return Image  # noqa: F401

def _yaml():
    import yaml; return yaml  # noqa: F401

def _huggingface_hub():
    from huggingface_hub import HfApi; return HfApi  # noqa: F401

# -- core logic -------------------------------------------------------------

def _parse_episode(path: Path) -> Optional[Dict[str, List[Any]]]:
    """Parse a single episode file (JSON, JSONL, or CSV) into columnar dict."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, list):
                data = [data]
        elif suffix == ".jsonl":
            data = [json.loads(ln) for ln in open(path, "r", encoding="utf-8") if ln.strip()]
        elif suffix == ".csv":
            with open(path, "r", encoding="utf-8") as fh:
                data = list(csv.DictReader(fh))
        else:
            return None
        result: Dict[str, List[Any]] = {}
        for row in data:
            for k, v in row.items():
                result.setdefault(k, []).append(v)
        return result if result else None
    except Exception as exc:
        logger.warning("Skipping %s: %s", path.name, exc)
        return None


def _write_meta(out_dir: Path, n_episodes: int, fps: float, chunk_size: int) -> None:
    """Write LeRobot meta/ files: info.json, dataset_info.json, episodes.jsonl."""
    meta = out_dir / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    info = {
        "codebase_version": "v2.0", "fps": fps, "total_episodes": n_episodes,
        "total_frames": n_episodes * chunk_size,
        "total_chunks": (n_episodes + chunk_size - 1) // chunk_size,
        "chunks_size": chunk_size, "episodes_data_index": None,
    }
    for name in ("info.json", "dataset_info.json"):
        (meta / name).write_text(json.dumps(info, indent=2), encoding="utf-8")
    with open(meta / "episodes.jsonl", "w", encoding="utf-8") as fh:
        for idx in range(n_episodes):
            fh.write(json.dumps({"episode_index": idx, "tasks": [], "length": chunk_size}) + "\n")


def _write_chunks(out_dir: Path, episodes: List[Dict[str, List[Any]]], chunk_size: int) -> None:
    """Write episode data into LeRobot chunk-NNNNNN/episode_NNNNNN.json files."""
    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    n_chunks = (len(episodes) + chunk_size - 1) // chunk_size
    for ci in range(n_chunks):
        chunk_dir = data_dir / f"chunk-{ci:06d}"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        for ei in range(ci * chunk_size, min((ci + 1) * chunk_size, len(episodes))):
            (chunk_dir / f"episode_{ei:06d}.json").write_text(
                json.dumps(episodes[ei], indent=2), encoding="utf-8")


def export_tarball(
    tarball_path: str, output_dir: str, *,
    repo_id: Optional[str] = None, push: bool = False,
    fps: float = 30.0, chunk_size: int = 50,
) -> int:
    """Extract *tarball_path*, convert episodes, write LeRobot structure to *output_dir*.

    Returns 0 on success, 1 on failure.
    """
    out = Path(output_dir)
    if not os.path.isfile(tarball_path):
        logger.error("Tarball not found: %s", tarball_path)
        return 1
    with tempfile.TemporaryDirectory(prefix="lerobot_export_") as tmpdir:
        with tarfile.open(tarball_path, "r:*") as tar:
            tar.extractall(tmpdir, filter="data")
        episode_files = sorted(
            p for p in Path(tmpdir).rglob("*")
            if p.is_file() and p.suffix.lower() in {".json", ".jsonl", ".csv"})
        if not episode_files:
            logger.error("No episode files (json/jsonl/csv) found in tarball")
            return 1
        episodes: List[Dict[str, List[Any]]] = []
        for ef in episode_files:
            parsed = _parse_episode(ef)
            if parsed is not None:
                episodes.append(parsed)
        if not episodes:
            logger.error("No valid episode data could be parsed")
            return 1
        _write_meta(out, len(episodes), fps, chunk_size)
        _write_chunks(out, episodes, chunk_size)
        logger.info("Exported %d episodes → %s", len(episodes), out)
    if push and repo_id:
        try:
            api = _huggingface_hub()()
            api.upload_folder(folder_path=str(out), repo_id=repo_id, repo_type="dataset")
            logger.info("Pushed to https://huggingface.co/datasets/%s", repo_id)
        except Exception as exc:
            logger.error("Push failed: %s", exc)
            return 1
    return 0


# -- CLI --------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    """Entry-point with argparse CLI."""
    parser = argparse.ArgumentParser(
        description="Convert a tarball of robot episodes to LeRobot HuggingFace format.")
    parser.add_argument("tarball", help="Path to input .tar.gz / .tar file")
    parser.add_argument("--output", "-o", required=True, help="Output directory for LeRobot dataset")
    parser.add_argument("--repo-id", default=None, help="HuggingFace repo-id (org/name) for optional push")
    parser.add_argument("--push", action="store_true", help="Push output to HuggingFace Hub after export")
    parser.add_argument("--fps", type=float, default=30.0, help="Frames per second (default 30)")
    parser.add_argument("--chunk-size", type=int, default=50, help="Frames per chunk (default 50)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    return export_tarball(
        args.tarball, args.output, repo_id=args.repo_id, push=args.push,
        fps=args.fps, chunk_size=args.chunk_size)


if __name__ == "__main__":
    sys.exit(main())
