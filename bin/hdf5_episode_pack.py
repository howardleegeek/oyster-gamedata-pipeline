#!/usr/bin/env python3
"""
hdf5_episode_pack.py - Pack episode data into a single HDF5 file.

Follows BEHAVIOR-1K / OmniGibson patterns to pack all episode data
(actions, depth maps, segmentation masks, IMU readings) into one HDF5 file.
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

_h5py, _np = None, None


def _get_h5py():
    """Lazily import h5py."""
    global _h5py
    if _h5py is None:
        import h5py
        _h5py = h5py
    return _h5py


def _get_np():
    """Lazily import numpy."""
    global _np
    if _np is None:
        import numpy
        _np = numpy
    return _np


PathLike = Union[str, Path]
logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging level based on verbosity flag."""
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(levelname)s: %(message)s")


def validate_dependencies() -> None:
    """Validate that required dependencies are available."""
    try:
        _get_h5py()
    except ImportError as e:
        raise ImportError("h5py is required: pip install h5py") from e
    try:
        _get_np()
    except ImportError as e:
        raise ImportError("numpy is required: pip install numpy") from e


def discover_episode_files(input_dir: Path) -> Dict[str, List[Path]]:
    """Discover episode data files by type in the input directory."""
    files: Dict[str, List[Path]] = {"actions": [], "depth": [], "seg": [], "imu": [], "metadata": []}
    if not input_dir.is_dir():
        return files
    for f in sorted(input_dir.rglob("*")):
        if not f.is_file():
            continue
        name, suffix = f.name.lower(), f.suffix.lower()
        if "action" in name or "act" in name:
            files["actions"].append(f)
        elif "depth" in name or "dpt" in name:
            files["depth"].append(f)
        elif "seg" in name or "mask" in name or "semantic" in name:
            files["seg"].append(f)
        elif "imu" in name or "inertial" in name:
            files["imu"].append(f)
        elif suffix in [".json", ".yaml", ".yml"] and ("meta" in name or "info" in name):
            files["metadata"].append(f)
    return files


def load_data_file(file_path: Path) -> Optional[Any]:
    """Load data from a file based on its extension."""
    np = _get_np()
    suffix = file_path.suffix.lower()
    try:
        if suffix == ".json":
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return np.array(data) if isinstance(data, list) else data
        elif suffix == ".npy":
            return np.load(file_path)
        elif suffix == ".npz":
            return dict(np.load(file_path))
        elif suffix == ".csv":
            return np.loadtxt(file_path, delimiter=",")
        elif suffix in [".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp"]:
            try:
                from PIL import Image
                return np.array(Image.open(file_path))
            except ImportError:
                logger.warning(f"PIL not available for image: {file_path}")
                return None
    except Exception as e:
        logger.warning(f"Failed to load {file_path}: {e}")
    return None


def pack_episode(input_dir: Path, output_path: Path, compression: Optional[str] = None,
                 compression_opts: Optional[int] = None) -> Tuple[int, int]:
    """Pack all episode data into a single HDF5 file. Returns (files_packed, files_skipped)."""
    h5py, np = _get_h5py(), _get_np()
    files_by_type = discover_episode_files(input_dir)
    files_packed, files_skipped = 0, 0
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with h5py.File(output_path, "w") as h5f:
        h5f.attrs["episode_path"] = str(input_dir)
        h5f.attrs["packer_version"] = "1.0.0"
        for data_type, files in files_by_type.items():
            if not files:
                continue
            group = h5f.create_group(data_type)
            for file_path in sorted(files):
                data = load_data_file(file_path)
                if data is None:
                    files_skipped += 1
                    continue
                dset_name = file_path.stem
                if isinstance(data, np.ndarray):
                    kwargs = {}
                    if compression:
                        kwargs["compression"] = compression
                        if compression_opts and compression == "gzip":
                            kwargs["compression_opts"] = compression_opts
                    try:
                        group.create_dataset(dset_name, data=data, **kwargs)
                        files_packed += 1
                    except Exception as e:
                        logger.warning(f"Failed to pack {file_path}: {e}")
                        files_skipped += 1
                elif isinstance(data, dict):
                    group.attrs[dset_name] = json.dumps(data)
                    files_packed += 1
                else:
                    group.attrs[dset_name] = data
                    files_packed += 1
    return files_packed, files_skipped


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point for HDF5 episode packing."""
    parser = argparse.ArgumentParser(description="Pack episode data into a single HDF5 file.")
    parser.add_argument("input_dir", type=Path, help="Input directory containing episode data files")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Output HDF5 file path")
    parser.add_argument("--compress", choices=["gzip", "lzf", "szip"], default=None,
                        help="Compression algorithm for HDF5 datasets")
    parser.add_argument("--level", type=int, default=4, help="Gzip compression level (0-9)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    try:
        validate_dependencies()
    except ImportError as e:
        logger.error(str(e))
        return 1

    if not args.input_dir.is_dir():
        logger.error(f"Input directory not found: {args.input_dir}")
        return 1

    try:
        packed, skipped = pack_episode(
            args.input_dir, args.output, compression=args.compress,
            compression_opts=args.level if args.compress == "gzip" else None)
        logger.info(f"Packed {packed} files, skipped {skipped} files -> {args.output}")
        return 0
    except Exception as e:
        logger.error(f"Failed to pack episode: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())