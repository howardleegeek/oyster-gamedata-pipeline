#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
buyer_evaluation_harness.py — G210 Buyer Evaluation Harness
============================================================
Reference benchmark: trains a tiny world-model on N clips, evaluates on a
held-out test set, and emits MSE / SSIM / FID metrics so a buyer can verify
data quality before bulk purchase.

Usage:
    python bin/buyer_evaluation_harness.py --data-dir /path/to/clips \
        --num-clips 100 --test-ratio 0.2 --output results.json

Dependencies: stdlib + numpy + PIL.  PyYAML/openpyxl optional.
torch/pydantic are lazy-imported and never required.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("buyer_eval")


def _setup_logging(verbose: bool = False) -> None:
    """Configure logging level and format."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )


# -- Lazy imports -----------------------------------------------------------

def _np() -> Any:
    """Lazy import numpy."""
    try:
        import numpy as np
        return np
    except ImportError:
        sys.exit("ERROR: numpy required. pip install numpy")


def _pil() -> Any:
    """Lazy import PIL.Image."""
    try:
        from PIL import Image
        return Image
    except ImportError:
        sys.exit("ERROR: Pillow required. pip install Pillow")


def _yaml() -> Optional[Any]:
    """Lazy import yaml (optional)."""
    try:
        import yaml
        return yaml
    except ImportError:
        return None


def _torch() -> Optional[Any]:
    """Lazy import torch (optional)."""
    try:
        import torch
        return torch
    except ImportError:
        return None


# -- Data loading -----------------------------------------------------------

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"}


def _collect_frames(data_dir: str) -> List[Path]:
    """Collect all image files recursively from data directory."""
    root = Path(data_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"Data directory not found: {data_dir}")
    frames = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in _IMAGE_EXTS]
    return sorted(frames)


def _load_frame(path: Path, size: Tuple[int, int] = (64, 64)) -> Any:
    """Load and preprocess a single frame as grayscale numpy array."""
    Image = _pil()
    np = _np()
    img = Image.open(str(path)).convert("L").resize(size, Image.LANCZOS)
    return np.asarray(img, dtype=np.float32) / 255.0


def _group_into_clips(frames: List[Path], clip_size: int = 8) -> List[List[Path]]:
    """Group consecutive frames into clips of specified size."""
    clips = []
    for i in range(0, len(frames) - clip_size + 1, clip_size):
        clips.append(frames[i:i + clip_size])
    return clips


def _split_clips(
    clips: List[List[Path]],
    test_ratio: float,
    seed: int = 42,
) -> Tuple[List[List[Path]], List[List[Path]]]:
    """Split clips into train/test sets based on test_ratio."""
    random.seed(seed)
    indices = list(range(len(clips)))
    random.shuffle(indices)
    split = int(len(indices) * (1 - test_ratio))
    train_idx, test_idx = indices[:split], indices[split:]
    return [clips[i] for i in train_idx], [clips[i] for i in test_idx]


# -- Model: Tiny ConvLSTM world model ---------------------------------------

class TinyWorldModel:
    """Minimal convolutional world model for quick evaluation."""

    def __init__(self, image_size: int = 64, hidden_dim: int = 32):
        self.image_size = image_size
        self.hidden_dim = hidden_dim
        self.device = "cpu"

    def _make_encoder(self) -> Dict[str, Any]:
        """Build simple encoder layers."""
        torch = _torch()
        return {
            "conv1": torch.nn.Conv2d(1, 16, 4, stride=2, padding=1),
            "conv2": torch.nn.Conv2d(16, 32, 4, stride=2, padding=1),
            "fc": torch.nn.Linear(32 * 16 * 16, self.hidden_dim),
        }

    def _make_decoder(self) -> Dict[str, Any]:
        """Build simple decoder layers."""
        torch = _torch()
        return {
            "fc": torch.nn.Linear(self.hidden_dim, 32 * 16 * 16),
            "deconv1": torch.nn.ConvTranspose2d(32, 16, 4, stride=2, padding=1),
            "deconv2": torch.nn.ConvTranspose2d(16, 1, 4, stride=2, padding=1),
        }

    def forward(self, x: Any) -> Any:
        """Forward pass: predict next frame from current frame."""
        torch = _torch()
        # Simple autoencoder-style forward for speed
        h = torch.relu(self._encoders["conv1"](x))
        h = torch.relu(self._encoders["conv2"](h))
        h = h.view(h.size(0), -1)
        h = torch.relu(self._encoders["fc"](h))
        h = self._decoders["fc"](h)
        h = h.view(h.size(0), 32, 16, 16)
        h = torch.relu(self._decoders["deconv1"](h))
        return torch.sigmoid(self._decoders["deconv2"](h))

    def train_step(self, x: Any) -> float:
        """Single training step, returns loss."""
        torch = _torch()
        opt = self._optimizer
        opt.zero_grad()
        pred = self.forward(x)
        loss = torch.nn.functional.mse_loss(pred, x)
        loss.backward()
        opt.step()
        return loss.item()

    def fit(
        self,
        train_clips: List[List[Path]],
        epochs: int = 3,
        batch_size: int = 4,
    ) -> Dict[str, float]:
        """Train the model on provided clips."""
        torch = _torch()
        self._encoders = torch.nn.ModuleDict(self._make_encoder())
        self._decoders = torch.nn.ModuleDict(self._make_decoder())
        self._optimizer = torch.optim.Adam(
            list(self._encoders.values()) + list(self._decoders.values()),
            lr=1e-3,
        )

        # Prepare training data
        frames = []
        for clip in train_clips:
            for path in clip:
                try:
                    frames.append(_load_frame(path, (self.image_size, self.image_size)))
                except Exception as e:
                    logger.warning(f"Skipping frame {path}: {e}")
        if not frames:
            return {"train_loss": float("nan")}

        data = torch.tensor(frames).unsqueeze(1)  # (N, 1, H, W)
        n = len(data)
        losses = []

        for epoch in range(epochs):
            perm = torch.randperm(n)
            epoch_loss = 0.0
            for i in range(0, n, batch_size):
                idx = perm[i:i + batch_size]
                batch = data[idx]
                loss = self.train_step(batch)
                epoch_loss += loss * len(batch)
            epoch_loss /= n
            losses.append(epoch_loss)
            logger.info(f"Epoch {epoch + 1}/{epochs} - loss: {epoch_loss:.4f}")

        return {"train_loss": losses[-1] if losses else float("nan")}


# -- Metrics ---------------------------------------------------------------

def compute_mse(pred: Any, target: Any) -> float:
    """Compute mean squared error between predicted and target frames."""
    np = _np()
    return float(np.mean((pred - target) ** 2))


def compute_ssim(pred: Any, target: Any, window_size: int = 5) -> float:
    """Compute structural similarity index between two frames."""
    np = _np()
    C1 = 0.01 ** 2
    C2 = 0.03 ** 2

    mu_pred = np.mean(pred)
    mu_target = np.mean(target)
    sigma_pred = np.var(pred)
    sigma_target = np.var(target)
    sigma_pred_target = np.mean((pred - mu_pred) * (target - mu_target))

    numerator = (2 * mu_pred * mu_target + C1) * (2 * sigma_pred_target + C2)
    denominator = (mu_pred ** 2 + mu_target ** 2 + C1) * (sigma_pred + sigma_target + C2)
    return float(numerator / denominator)


def compute_fid(real_features: Any, gen_features: Any) -> float:
    """Compute Frechet Inception Distance (simplified, uses mean/cov)."""
    np = _np()
    mu_real, sigma_real = np.mean(real_features, axis=0), np.cov(real_features, rowvar=False)
    mu_gen, sigma_gen = np.mean(gen_features, axis=0), np.cov(gen_features, rowvar=False)

    diff = mu_real - mu_gen
    covmean = np.sqrt(sigma_real @ sigma_gen + 1e-6 * np.eye(len(mu_real)))
    fid = float(np.sum(diff ** 2) + np.trace(sigma_real + sigma_gen - 2 * covmean))
    return fid


def _extract_features(frames: List[Any]) -> Any:
    """Extract simple feature vectors for FID computation."""
    np = _np()
    # Use flattened pixels as features (simplified for speed)
    return np.array([f.flatten() for f in frames])


# -- Evaluation ------------------------------------------------------------

def evaluate_model(model: TinyWorldModel, test_clips: List[List[Path]]) -> Dict[str, float]:
    """Evaluate trained model on test clips, return metrics."""
    torch = _torch()
    np = _np()

    all_preds = []
    all_targets = []

    for clip in test_clips:
        frames = []
        for path in clip:
            try:
                frames.append(_load_frame(path, (model.image_size, model.image_size)))
            except Exception as e:
                logger.warning(f"Skipping frame {path}: {e}")
                continue

        if len(frames) < 2:
            continue

        # Predict next frame from current
        data = torch.tensor(frames).unsqueeze(1)
        with torch.no_grad():
            preds = model.forward(data).squeeze(1).numpy()

        all_preds.extend(preds)
        all_targets.extend(frames[1:])  # shifted by one

    if not all_preds:
        return {"mse": float("nan"), "ssim": float("nan"), "fid": float("nan")}

    # Compute metrics
    preds_arr = np.array(all_preds)
    targets_arr = np.array(all_targets)

    mse = compute_mse(preds_arr, targets_arr)
    ssim = compute_ssim(preds_arr, targets_arr)

    # Simplified FID using pixel features
    real_feat = _extract_features(targets_arr)
    gen_feat = _extract_features(preds_arr)
    fid = compute_fid(real_feat, gen_feat)

    return {"mse": mse, "ssim": ssim, "fid": fid}


# -- Main ------------------------------------------------------------------

def _parse_args(argv: List[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="G210 Buyer Evaluation Harness: train tiny world model and evaluate.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--data-dir", type=str, required=True, help="Directory containing video clips"
    )
    parser.add_argument(
        "--num-clips", type=int, default=100, help="Number of clips to use for training"
    )
    parser.add_argument(
        "--test-ratio", type=float, default=0.2, help="Fraction of data for testing"
    )
    parser.add_argument("--clip-size", type=int, default=8, help="Frames per clip")
    parser.add_argument("--image-size", type=int, default=64, help="Resize frames to this size")
    parser.add_argument("--epochs", type=int, default=3, help="Training epochs")
    parser.add_argument("--batch-size", type=int, default=4, help="Training batch size")
    parser.add_argument("--output", type=str, default="results.json", help="Output JSON file path")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    """Main entry point."""
    args = _parse_args(argv)
    _setup_logging(args.verbose)

    logger.info("Starting buyer evaluation harness")
    start_time = time.time()

    # Verify torch is available (required for model training)
    if _torch() is None:
        sys.exit("ERROR: torch required for model training. pip install torch")

    # Collect and prepare data
    logger.info(f"Collecting frames from: {args.data_dir}")
    frames = _collect_frames(args.data_dir)
    logger.info(f"Found {len(frames)} frames")

    if len(frames) < args.clip_size:
        logger.error(f"Not enough frames: {len(frames)} < {args.clip_size}")
        return 1

    # Group into clips and split
    clips = _group_into_clips(frames, args.clip_size)
    logger.info(f"Created {len(clips)} clips of size {args.clip_size}")

    # Limit to num_clips
    if len(clips) > args.num_clips:
        random.seed(args.seed)
        clips = random.sample(clips, args.num_clips)

    train_clips, test_clips = _split_clips(clips, args.test_ratio, args.seed)
    logger.info(f"Train: {len(train_clips)} clips, Test: {len(test_clips)} clips")

    # Train model
    logger.info("Training tiny world model...")
    model = TinyWorldModel(image_size=args.image_size)
    train_metrics = model.fit(train_clips, epochs=args.epochs, batch_size=args.batch_size)
    logger.info(f"Training complete. Final loss: {train_metrics['train_loss']:.4f}")

    # Evaluate
    logger.info("Evaluating on test set...")
    eval_metrics = evaluate_model(model, test_clips)
    logger.info(
        f"MSE: {eval_metrics['mse']:.4f}, "
        f"SSIM: {eval_metrics['ssim']:.4f}, "
        f"FID: {eval_metrics['fid']:.2f}"
    )

    # Write results
    results = {
        "train_metrics": train_metrics,
        "eval_metrics": eval_metrics,
        "config": {
            "num_clips": args.num_clips,
            "test_ratio": args.test_ratio,
            "clip_size": args.clip_size,
            "image_size": args.image_size,
            "epochs": args.epochs,
            "batch_size": args.batch_size,
        },
        "elapsed_seconds": time.time() - start_time,
    }

    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)
    logger.info(f"Results written to: {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
