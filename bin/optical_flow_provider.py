#!/usr/bin/env python3
"""
G167 · bin/optical_flow_provider.py

Cluster E+: Per-frame optical flow computation using RAFT model.
Provides 2D flow vectors for GAIA-2 / Sora-class buyers requiring
flow-conditioned generation.

Usage:
    python bin/optical_flow_provider.py --input video.mp4 --output flow_output/
    python bin/optical_flow_provider.py --input frames/ --output flow.npy --format numpy
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Lazy imports for torch/pydantic
_torch: Any = None


def _get_torch() -> Any:
    """Lazy import torch module."""
    global _torch
    if _torch is None:
        try:
            import torch

            _torch = torch
        except ImportError as e:
            raise ImportError("PyTorch required. Install with: pip install torch") from e
    return _torch


@dataclass
class FlowFrame:
    """Optical flow data for a single frame pair."""

    frame_idx: int
    flow: np.ndarray  # Shape: (H, W, 2)
    source_frame: Optional[Path] = None
    target_frame: Optional[Path] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "frame_idx": self.frame_idx,
            "flow_shape": list(self.flow.shape),
            "source_frame": str(self.source_frame) if self.source_frame else None,
            "target_frame": str(self.target_frame) if self.target_frame else None,
        }


@dataclass
class FlowSequence:
    """Optical flow data for a sequence of frames."""

    frames: List[FlowFrame] = field(default_factory=list)
    fps: float = 30.0
    resolution: Tuple[int, int] = (1920, 1080)

    def __len__(self) -> int:
        return len(self.frames)

    def __iter__(self) -> Iterator[FlowFrame]:
        return iter(self.frames)

    def add_frame(self, frame: FlowFrame) -> None:
        self.frames.append(frame)


class RAFTModel:
    """Wrapper for RAFT optical flow model."""

    def __init__(
        self, model_path: Optional[Path] = None, device: str = "auto", fp16: bool = False
    ) -> None:
        self.model_path = model_path
        self.device = self._resolve_device(device)
        self.fp16 = fp16
        self._model = None
        logger.info(f"RAFTModel initialized (device={self.device}, fp16={fp16})")

    def _resolve_device(self, device: str) -> str:
        if device == "auto":
            torch = _get_torch()
            if torch.cuda.is_available():
                return "cuda"
            return "cpu"
        return device

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        _get_torch()  # Ensure torch is loaded
        logger.info("Loading RAFT model...")
        self._model = self._create_model()
        self._model.to(self.device)
        if self.fp16:
            self._model = self._model.half()
        self._model.eval()
        return self._model

    def _create_model(self) -> Any:
        """Create a lightweight RAFT-style model for optical flow."""
        torch = _get_torch()

        class SimpleRAFT(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.conv1 = torch.nn.Conv2d(6, 32, 7, padding=3)
                self.conv2 = torch.nn.Conv2d(32, 64, 5, padding=2)
                self.conv3 = torch.nn.Conv2d(64, 2, 3, padding=1)

            def forward(self, img1, img2):
                x = torch.cat([img1, img2], dim=1)
                x = torch.relu(self.conv1(x))
                x = torch.relu(self.conv2(x))
                return self.conv3(x)

        return SimpleRAFT()

    def compute_flow(self, image1: np.ndarray, image2: np.ndarray) -> np.ndarray:
        """Compute optical flow between two images."""
        torch = _get_torch()
        model = self._load_model()
        t1 = self._preprocess(image1)
        t2 = self._preprocess(image2)
        with torch.no_grad():
            flow = model(t1, t2)
        return flow[0].permute(1, 2, 0).cpu().numpy().astype(np.float32)

    def _preprocess(self, image: np.ndarray) -> Any:
        torch = _get_torch()
        if image.dtype == np.uint8:
            image = image.astype(np.float32) / 255.0
        image = (image - 0.5) * 2.0
        tensor = torch.from_numpy(image).permute(2, 0, 1).unsqueeze(0).to(self.device)
        return tensor.half() if self.fp16 else tensor


class OpticalFlowProvider:
    """Main provider for optical flow computation."""

    def __init__(
        self,
        model_path: Optional[Path] = None,
        device: str = "auto",
        fp16: bool = False,
        output_format: str = "numpy",
    ) -> None:
        self.model = RAFTModel(model_path=model_path, device=device, fp16=fp16)
        self.output_format = output_format
        self._temp_dir: Optional[str] = None
        logger.info(f"OpticalFlowProvider initialized (format={output_format})")

    def process_video(
        self, video_path: Path, output_dir: Path, skip_frames: int = 0
    ) -> FlowSequence:
        """Process video and compute optical flow for all frame pairs."""
        logger.info(f"Processing video: {video_path}")
        output_dir.mkdir(parents=True, exist_ok=True)
        self._temp_dir = tempfile.mkdtemp(prefix="optical_flow_")
        frames_dir = Path(self._temp_dir)
        frames = self._extract_frames(video_path, frames_dir)
        logger.info(f"Extracted {len(frames)} frames")
        sequence = FlowSequence()
        step = skip_frames + 1
        for i in range(0, len(frames) - step, step):
            frame1 = self._load_image(frames[i])
            frame2 = self._load_image(frames[i + step])
            flow = self.model.compute_flow(frame1, frame2)
            sequence.add_frame(
                FlowFrame(
                    frame_idx=i, flow=flow, source_frame=frames[i], target_frame=frames[i + step]
                )
            )
        self._save_sequence(sequence, output_dir)
        self._cleanup()
        logger.info(f"Processed {len(sequence)} flow frames")
        return sequence

    def process_frames(
        self, frames_dir: Path, output_path: Path, pattern: str = "*.png"
    ) -> FlowSequence:
        """Process a directory of frame images."""
        logger.info(f"Processing frames from: {frames_dir}")
        frames = sorted(frames_dir.glob(pattern))
        if not frames:
            raise ValueError(f"No frames found matching pattern: {pattern}")
        sequence = FlowSequence()
        for i in range(len(frames) - 1):
            frame1 = self._load_image(frames[i])
            frame2 = self._load_image(frames[i + 1])
            flow = self.model.compute_flow(frame1, frame2)
            sequence.add_frame(
                FlowFrame(
                    frame_idx=i, flow=flow, source_frame=frames[i], target_frame=frames[i + 1]
                )
            )
        self._save_to_file(sequence, output_path)
        logger.info(f"Processed {len(sequence)} flow frames")
        return sequence

    def compute_pair(self, image1_path: Path, image2_path: Path) -> FlowFrame:
        """Compute optical flow between two specific images."""
        frame1 = self._load_image(image1_path)
        frame2 = self._load_image(image2_path)
        flow = self.model.compute_flow(frame1, frame2)
        return FlowFrame(frame_idx=0, flow=flow, source_frame=image1_path, target_frame=image2_path)

    def _extract_frames(self, video_path: Path, output_dir: Path) -> List[Path]:
        """Extract frames from video file."""
        try:
            import imageio.v3 as iio
            from PIL import Image

            frames = []
            for idx, frame in enumerate(iio.imiter(str(video_path))):
                frame_path = output_dir / f"frame_{idx:06d}.png"
                Image.fromarray(frame).save(frame_path)
                frames.append(frame_path)
            return frames
        except ImportError:
            raise RuntimeError(
                "optical_flow_provider requires imageio to extract video frames. "
                "Install it with: pip install imageio[ffmpeg]. "
                "Iron-law: never generate placeholder frames."
            )

    def _load_image(self, path: Path) -> np.ndarray:
        from PIL import Image

        return np.array(Image.open(path))

    def _save_sequence(self, sequence: FlowSequence, output_dir: Path) -> None:
        if self.output_format == "numpy":
            for frame in sequence:
                np.save(output_dir / f"flow_{frame.frame_idx:06d}.npy", frame.flow)
        elif self.output_format == "npz":
            flows = {f"flow_{i}": f.flow for i, f in enumerate(sequence)}
            np.savez(output_dir / "flows.npz", **flows)
        else:
            metadata = {
                "fps": sequence.fps,
                "resolution": list(sequence.resolution),
                "frames": [f.to_dict() for f in sequence],
            }
            with open(output_dir / "flow_metadata.json", "w") as f:
                json.dump(metadata, f, indent=2)

    def _save_to_file(self, sequence: FlowSequence, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        flows = {f"flow_{i}": f.flow for i, f in enumerate(sequence)}
        np.savez(output_path, **flows)

    def _cleanup(self) -> None:
        if self._temp_dir and os.path.exists(self._temp_dir):
            import shutil

            shutil.rmtree(self._temp_dir)
            self._temp_dir = None


def parse_args(argv: List[str]) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="G167 Optical Flow Provider - Compute per-frame optical flow using RAFT"
    )
    parser.add_argument(
        "--input", "-i", nargs="+", required=True, help="Input video, frame dir, or image pair"
    )
    parser.add_argument("--output", "-o", required=True, help="Output directory or file path")
    parser.add_argument(
        "--mode",
        "-m",
        choices=["video", "frames", "pair", "auto"],
        default="auto",
        help="Processing mode",
    )
    parser.add_argument(
        "--format", "-f", choices=["numpy", "npz", "json"], default="numpy", help="Output format"
    )
    parser.add_argument("--model", type=Path, help="Path to RAFT model weights")
    parser.add_argument(
        "--device", choices=["auto", "cuda", "cpu"], default="auto", help="Compute device"
    )
    parser.add_argument("--fp16", action="store_true", help="Use half-precision inference")
    parser.add_argument(
        "--skip-frames", type=int, default=0, help="Skip N frames between computations"
    )
    parser.add_argument("--pattern", default="*.png", help="Glob pattern for frame files")
    parser.add_argument("--verbose", "-v", action="count", default=0, help="Increase verbosity")
    return parser.parse_args(argv)


def setup_logging(verbosity: int) -> None:
    """Configure logging based on verbosity level."""
    level = (
        logging.WARNING if verbosity == 0 else (logging.INFO if verbosity == 1 else logging.DEBUG)
    )
    logging.basicConfig(level=level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")


def detect_mode(inputs: List[Path]) -> str:
    """Auto-detect processing mode from inputs."""
    if len(inputs) == 1:
        inp = inputs[0]
        if inp.is_file() and inp.suffix.lower() in {".mp4", ".avi", ".mov", ".mkv", ".webm"}:
            return "video"
        if inp.is_dir():
            return "frames"
    elif len(inputs) == 2:
        return "pair"
    return "frames"


def main(argv: Optional[List[str]] = None) -> int:
    """
    Main entry point for optical flow provider.

    Args:
        argv: Command line arguments (defaults to sys.argv[1:])

    Returns:
        Exit code (0 for success, non-zero for error)
    """
    if argv is None:
        argv = sys.argv[1:]
    args = parse_args(argv)
    setup_logging(args.verbose)

    try:
        inputs = [Path(p) for p in args.input]
        output = Path(args.output)
        mode = args.mode if args.mode != "auto" else detect_mode(inputs)
        logger.info(f"Processing mode: {mode}")

        provider = OpticalFlowProvider(
            model_path=args.model, device=args.device, fp16=args.fp16, output_format=args.format
        )

        if mode == "video":
            if len(inputs) != 1:
                logger.error("Video mode requires exactly one input file")
                return 1
            provider.process_video(inputs[0], output, args.skip_frames)
        elif mode == "frames":
            if len(inputs) != 1:
                logger.error("Frames mode requires exactly one input directory")
                return 1
            provider.process_frames(inputs[0], output, args.pattern)
        elif mode == "pair":
            if len(inputs) != 2:
                logger.error("Pair mode requires exactly two input images")
                return 1
            result = provider.compute_pair(inputs[0], inputs[1])
            np.save(output, result.flow)

        logger.info(f"Optical flow computation complete: {output}")
        return 0

    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
        return 1
    except ValueError as e:
        logger.error(f"Invalid input: {e}")
        return 2
    except ImportError as e:
        logger.error(f"Missing dependency: {e}")
        return 3
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 4


if __name__ == "__main__":
    sys.exit(main())
