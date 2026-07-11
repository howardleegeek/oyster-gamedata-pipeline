#!/usr/bin/env python3
"""Autoresearch: DepthAnything V2 vs Marigold MAE on 50 Minecraft Z-buffer frames.

Compares predicted depth maps from two monocular depth estimation models
against ground-truth Z-buffer renders from Minecraft. Produces per-frame
and aggregate quality metrics (AbsRel, RMSE, δ<1.25) and writes a
summary report to stdout and optionally to an Excel workbook.

Usage:
    python bin/autoresearch_depth_quality.py --gt-dir /path/to/gt_zbuf \
        --da-dir /path/to/da_preds --mg-dir /path/to/mg_preds --output report.xlsx
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

# Lazy imports for heavy optional deps
_np: Any = None
_PIL_Image: Any = None
_openpyxl: Any = None

def _import_numpy() -> Any:
    global _np
    if _np is None:
        import numpy as _np
    return _np

def _import_pil() -> Any:
    global _PIL_Image
    if _PIL_Image is None:
        from PIL import Image as _PIL_Image
    return _PIL_Image

def _import_openpyxl() -> Any:
    global _openpyxl
    if _openpyxl is None:
        import openpyxl as _openpyxl
    return _openpyxl

def _compute_metrics(gt: "np.ndarray", pred: "np.ndarray") -> Dict[str, float]:
    """Return AbsRel, RMSE, and delta-accuracy for one frame pair."""
    np = _import_numpy()
    eps = 1e-6
    gt, pred = gt.astype(np.float64), pred.astype(np.float64)
    mask = gt > eps
    if not mask.any():
        return {"abs_rel": float("nan"), "rmse": float("nan"), "delta_1": float("nan")}
    g, p = gt[mask], pred[mask]
    abs_rel = float(np.mean(np.abs(g - p) / (g + eps)))
    rmse = float(np.sqrt(np.mean((g - p) ** 2)))
    delta = np.maximum(g / (p + eps), p / (g + eps))
    return {"abs_rel": abs_rel, "rmse": rmse, "delta_1": float(np.mean(delta < 1.25))}

def _load_image(path: Path) -> "np.ndarray":
    """Load an image file and return as float64 numpy array (H, W)."""
    Image = _import_pil()
    np = _import_numpy()
    return np.array(Image.open(str(path)).convert("L"), dtype=np.float64)

def _load_zbuffer(path: Path) -> "np.ndarray":
    """Load a ground-truth Z-buffer (.npy or image)."""
    np = _import_numpy()
    if path.suffix == ".npy":
        return np.load(str(path)).astype(np.float64).squeeze()
    return _load_image(path)

def _collect_frames(gt_dir: Path, pred_dir: Path) -> List[Tuple[Path, Path]]:
    """Pair ground-truth and prediction files by stem name."""
    gt_map = {p.stem: p for p in gt_dir.glob("*") if p.is_file()}
    pred_map = {p.stem: p for p in pred_dir.glob("*") if p.is_file()}
    return [(gt_map[s], pred_map[s]) for s in sorted(set(gt_map) & set(pred_map))]

def run_comparison(
    gt_dir: Path, pred_dirs: Dict[str, Path], max_frames: int = 50,
) -> Dict[str, Any]:
    """Run depth-quality comparison across all model prediction directories.

    Args:
        gt_dir: Directory containing ground-truth Z-buffer frames.
        pred_dirs: Mapping of model name → prediction directory.
        max_frames: Maximum number of frames to evaluate.

    Returns:
        Nested dict with per-model per-frame metrics and aggregates.
    """
    results: Dict[str, Any] = {"models": {}, "summary": {}}
    for model_name, pred_dir in pred_dirs.items():
        pairs = _collect_frames(gt_dir, pred_dir)[:max_frames]
        if not pairs:
            logger.warning("No matching frames for '%s' in %s", model_name, pred_dir)
            continue
        frame_metrics: List[Dict[str, float]] = []
        for gt_path, pred_path in pairs:
            gt, pred = _load_zbuffer(gt_path), _load_zbuffer(pred_path)
            np = _import_numpy()
            for arr in (gt, pred):
                lo, hi = arr.min(), arr.max()
                if hi > lo:
                    arr[:] = (arr - lo) / (hi - lo)
            frame_metrics.append(_compute_metrics(gt, pred))
        keys = ["abs_rel", "rmse", "delta_1"]
        agg = {k: float(np.mean([m[k] for m in frame_metrics])) for k in keys}
        results["models"][model_name] = {
            "n_frames": len(frame_metrics),
            "per_frame": frame_metrics,
            "aggregate": agg,
        }
    results["summary"] = {m: d["aggregate"] for m, d in results["models"].items()}
    return results

def _print_report(results: Dict[str, Any]) -> None:
    """Pretty-print the comparison report to stdout."""
    print("=" * 72)
    print("  DepthAnything V2  vs  Marigold MAE  —  Quality Report")
    print("=" * 72)
    for model, data in results["models"].items():
        a = data["aggregate"]
        print(f"\n  Model: {model}  ({data['n_frames']} frames)")
        print(f"    AbsRel : {a['abs_rel']:.4f}")
        print(f"    RMSE   : {a['rmse']:.4f}")
        print(f"    δ < 1.25: {a['delta_1']:.4f}")
    print("\n" + "=" * 72)

def _write_excel(results: Dict[str, Any], output_path: Path) -> None:
    """Write per-frame and aggregate metrics to an Excel workbook."""
    openpyxl = _import_openpyxl()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Aggregate"
    ws.append(["Model", "n_frames", "AbsRel", "RMSE", "Delta<1.25"])
    for model, data in results["models"].items():
        a = data["aggregate"]
        ws.append([model, data["n_frames"], a["abs_rel"], a["rmse"], a["delta_1"]])
    ws2 = wb.create_sheet("PerFrame")
    ws2.append(["Model", "Frame", "AbsRel", "RMSE", "Delta<1.25"])
    for model, data in results["models"].items():
        for i, m in enumerate(data["per_frame"]):
            ws2.append([model, i, m["abs_rel"], m["rmse"], m["delta_1"]])
    wb.save(str(output_path))
    logger.info("Excel report written to %s", output_path)

def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser for the CLI."""
    p = argparse.ArgumentParser(
        description="Compare DepthAnything V2 vs Marigold MAE on Minecraft Z-buffer frames."
    )
    p.add_argument("--gt-dir", type=Path, required=True, help="Ground-truth Z-buffer directory")
    p.add_argument("--da-dir", type=Path, required=True, help="DepthAnything V2 predictions")
    p.add_argument("--mg-dir", type=Path, required=True, help="Marigold MAE predictions")
    p.add_argument(
        "--max-frames", type=int, default=50, help="Max frames to evaluate (default: 50)"
    )
    p.add_argument("--output", type=Path, default=None, help="Optional Excel output path")
    p.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    return p

def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point. Returns 0 on success, non-zero on failure."""
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s  %(levelname)-7s  %(message)s")
    if not args.gt_dir.is_dir():
        logger.error("Ground-truth directory not found: %s", args.gt_dir)
        return 1
    pred_dirs: Dict[str, Path] = {"DepthAnythingV2": args.da_dir, "MarigoldMAE": args.mg_dir}
    for name, d in pred_dirs.items():
        if not d.is_dir():
            logger.error("Prediction directory not found for %s: %s", name, d)
            return 1
    with tempfile.TemporaryDirectory(prefix="autoresearch_depth_") as tmpdir:
        logger.debug("Temp scratch dir: %s", tmpdir)
        results = run_comparison(args.gt_dir, pred_dirs, max_frames=args.max_frames)
    _print_report(results)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        _write_excel(results, args.output)
    json.dumps(results)  # sanity: validate serialisable
    logger.info("Done.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
