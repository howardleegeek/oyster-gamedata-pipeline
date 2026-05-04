#!/usr/bin/env python3
"""
vendor_scenario_mac_only.py — Walkthrough: vendor on M1 Mac (no GPU).

Simulates a vendor deployment on Apple Silicon where the depth-provider
detects no CUDA-capable GPU and falls back to a CPU-based inference model.
Exercises the decision path, logs each step, and produces a JSON summary.

Usage:
    python3 bin/vendor_scenario_mac_only.py [--dry-run] [--verbose]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import sys
import tempfile
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _import_numpy():
    """Lazy-import numpy only when needed."""
    import numpy as np  # noqa: F401
    return np


def _import_torch():
    """Attempt torch import; return None when unavailable."""
    try:
        import torch  # noqa: F401
        return torch
    except ImportError:
        return None


def detect_platform() -> Dict[str, str]:
    """Return a dict describing the current platform."""
    return {
        "system": platform.system(),
        "machine": platform.machine(),
        "node": platform.node(),
        "python": platform.python_version(),
    }


def has_cuda_gpu() -> bool:
    """Check whether a CUDA-capable GPU is available."""
    torch = _import_torch()
    if torch is None:
        logger.info("torch not installed — assuming no CUDA GPU")
        return False
    return torch.cuda.is_available()


def select_depth_provider(use_gpu: bool) -> str:
    """Return the depth-provider backend name based on hardware."""
    return "cuda_depth_v2" if use_gpu else "cpu_depth_fallback"


def simulate_inference(provider: str, dry_run: bool = False) -> Dict[str, Any]:
    """
    Simulate a depth-inference run with the chosen provider.

    Parameters
    ----------
    provider : str
        Backend identifier (e.g. ``"cpu_depth_fallback"``).
    dry_run : bool
        When True, skip actual computation and return a stub result.

    Returns
    -------
    dict
        Summary containing provider, status, and a temp output path.
    """
    tmp_dir = tempfile.mkdtemp(prefix="vendor_depth_")
    output_path = os.path.join(tmp_dir, "depth_output.npy")

    if not dry_run:
        np = _import_numpy()
        dummy_depth = np.zeros((64, 64), dtype=np.float32)
        np.save(output_path, dummy_depth)
        logger.info("Wrote simulated depth map to %s", output_path)

    return {"provider": provider, "status": "ok", "output_path": output_path, "dry_run": dry_run}


def build_report(
    platform_info: Dict[str, str],
    provider: str,
    inference_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Assemble the final JSON-serialisable report."""
    return {
        "scenario": "vendor_mac_no_gpu",
        "platform": platform_info,
        "gpu_available": has_cuda_gpu(),
        "selected_provider": provider,
        "inference": inference_result,
    }


def main(argv: Optional[List[str]] = None) -> int:
    """
    Entry-point for the vendor M1-Mac walkthrough.

    Parameters
    ----------
    argv : list[str] | None
        Command-line arguments (defaults to ``sys.argv[1:]``).

    Returns
    -------
    int
        Exit code (0 on success, 1 on failure).
    """
    parser = argparse.ArgumentParser(description="Vendor scenario: M1 Mac depth-provider CPU fallback")
    parser.add_argument("--dry-run", action="store_true", help="Skip actual numpy/torch computation")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG-level logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
    )

    plat = detect_platform()
    logger.info("Platform: %s %s", plat["system"], plat["machine"])

    gpu_ok = has_cuda_gpu()
    logger.info("CUDA GPU available: %s", gpu_ok)

    provider = select_depth_provider(use_gpu=gpu_ok)
    logger.info("Selected depth provider: %s", provider)

    result = simulate_inference(provider, dry_run=args.dry_run)
    logger.info("Inference result: %s", result["status"])

    report = build_report(plat, provider, result)
    print(json.dumps(report, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
