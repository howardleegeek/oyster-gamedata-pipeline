#!/usr/bin/env python3
"""buyer_eval_harness.py — G210 Buyer Evaluation Harness (data-quality edition).

The existing :file:`bin/buyer_evaluation_harness.py` trains a tiny world-model
on a directory of frames and reports MSE/SSIM/FID — useful but heavy (requires
torch). This harness solves a different buyer need: *"before I spend $$$ on a
batch, give me a quick read on data diversity and coverage."*

It runs entirely on the metadata side of the buyer-spec v1 tarballs:
  - parses each clip's action_camera.json
  - computes:
      * trajectory diversity   (per-clip path length, bounding-box, span)
      * scene coverage         (gameinfo scene_name distribution)
      * route_type distribution per batch
      * action entropy         (Shannon entropy of keyCode and direction vectors)
      * fps / duration stability
  - emits a structured JSON report and an `eval_report.html` dashboard with
    inline-rendered charts (SVG, no JS libs) the buyer can open offline.

The harness has no Supabase / Vercel / GPU / torch deps. Pure stdlib + the SDK.
A buyer's CI can run it with::

    pip install oyster-gamedata-sdk
    python bin/buyer_eval_harness.py --batch-dir ./downloads/batch-A \\
        --output ./eval/

Usage::

    buyer_eval_harness --batch-dir <DIR>            # default JSON + HTML report
    buyer_eval_harness --batch-dir <DIR> --json-only
    buyer_eval_harness --tarball <T1> <T2> ...      # explicit list of tarballs
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import math
import statistics
import sys
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Make the SDK importable in-tree without `pip install -e .`
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_SDK_PATH = _REPO / "sdk" / "python"
if str(_SDK_PATH) not in sys.path:
    sys.path.insert(0, str(_SDK_PATH))

from oyster_gamedata_sdk import (  # noqa: E402
    ActionCameraFrame,
    GameDataSDKError,
    Tarball,
)

logger = logging.getLogger("buyer_eval_harness")

# ---------------------------------------------------------------------------
# Metrics types
# ---------------------------------------------------------------------------


@dataclass
class TrajectoryMetrics:
    """Per-clip trajectory statistics from action_camera frames."""

    path_length_m: float
    bbox_span_x: float
    bbox_span_y: float
    bbox_span_z: float
    mean_speed_mps: float
    max_speed_mps: float
    stationary_fraction: float
    n_frames: int


@dataclass
class ActionMetrics:
    key_code_counts: Dict[int, int]
    key_code_entropy_bits: float
    mouse_dx_std: float
    mouse_dy_std: float
    most_common_key: Optional[int]


@dataclass
class TimingMetrics:
    fps_mean: float
    fps_min: float
    fps_max: float
    fps_std: float
    duration_s: float


@dataclass
class ClipReport:
    clip_id: str
    source: str
    passed_lint: bool
    lint_pass_rate: float
    n_frames: int
    n_depth: int
    resolution: Tuple[int, int]
    game: str
    route_type_distribution: Dict[int, int]
    trajectory: TrajectoryMetrics
    actions: ActionMetrics
    timing: TimingMetrics
    scene_name: Optional[str]
    operator_id: Optional[str]
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = asdict(self)
        d["resolution"] = list(self.resolution)
        return d


@dataclass
class BatchReport:
    """Aggregate report across a batch (set of clips)."""

    batch_path: Optional[Path]
    n_clips: int
    n_passed: int
    overall_route_distribution: Dict[int, int]
    overall_scene_distribution: Dict[str, int]
    overall_operator_distribution: Dict[str, int]
    median_path_length_m: float
    median_n_frames: int
    median_action_entropy_bits: float
    median_stationary_fraction: float
    median_fps_std: float
    clips: List[ClipReport]
    elapsed_seconds: float = 0.0
    started_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "batch_path": str(self.batch_path) if self.batch_path else None,
            "started_at": self.started_at,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "summary": {
                "n_clips": self.n_clips,
                "n_passed_lint": self.n_passed,
                "median_path_length_m": round(self.median_path_length_m, 3),
                "median_n_frames": self.median_n_frames,
                "median_action_entropy_bits": round(self.median_action_entropy_bits, 4),
                "median_stationary_fraction": round(self.median_stationary_fraction, 4),
                "median_fps_std": round(self.median_fps_std, 4),
            },
            "distributions": {
                "route_type": dict(sorted(self.overall_route_distribution.items())),
                "scene_name": dict(self.overall_scene_distribution),
                "operator_id": dict(self.overall_operator_distribution),
            },
            "clips": [c.to_dict() for c in self.clips],
        }


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------


def _frame_distance(a: ActionCameraFrame, b: ActionCameraFrame) -> float:
    """Euclidean distance between two camera_position points."""
    dx = b.camera_position.x - a.camera_position.x
    dy = b.camera_position.y - a.camera_position.y
    dz = b.camera_position.z - a.camera_position.z
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _speed_magnitude(frame: ActionCameraFrame) -> float:
    s = frame.camera_speed
    return math.sqrt(s.x * s.x + s.y * s.y + s.z * s.z)


def compute_trajectory(frames: List[ActionCameraFrame]) -> TrajectoryMetrics:
    if not frames:
        return TrajectoryMetrics(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0)

    # Cumulative path length
    path_len = 0.0
    for i in range(1, len(frames)):
        path_len += _frame_distance(frames[i - 1], frames[i])

    xs = [f.camera_position.x for f in frames]
    ys = [f.camera_position.y for f in frames]
    zs = [f.camera_position.z for f in frames]

    speeds = [_speed_magnitude(f) for f in frames]
    stationary = sum(1 for s in speeds if s < 0.1) / len(frames)

    return TrajectoryMetrics(
        path_length_m=path_len,
        bbox_span_x=max(xs) - min(xs),
        bbox_span_y=max(ys) - min(ys),
        bbox_span_z=max(zs) - min(zs),
        mean_speed_mps=sum(speeds) / len(speeds),
        max_speed_mps=max(speeds),
        stationary_fraction=stationary,
        n_frames=len(frames),
    )


def _shannon_entropy_bits(counts: Iterable[int]) -> float:
    total = sum(counts)
    if total == 0:
        return 0.0
    h = 0.0
    for c in counts:
        if c <= 0:
            continue
        p = c / total
        h -= p * math.log2(p)
    return h


def compute_actions(frames: List[ActionCameraFrame]) -> ActionMetrics:
    key_counter: Counter[int] = Counter()
    mouse_dx: List[float] = []
    mouse_dy: List[float] = []
    for f in frames:
        for k in f.key_code:
            key_counter[k] += 1
        mouse_dx.append(f.mouse_dx)
        mouse_dy.append(f.mouse_dy)

    key_dict = dict(key_counter)
    entropy = _shannon_entropy_bits(key_dict.values())
    return ActionMetrics(
        key_code_counts=key_dict,
        key_code_entropy_bits=entropy,
        mouse_dx_std=statistics.pstdev(mouse_dx) if mouse_dx else 0.0,
        mouse_dy_std=statistics.pstdev(mouse_dy) if mouse_dy else 0.0,
        most_common_key=key_counter.most_common(1)[0][0] if key_counter else None,
    )


def compute_timing(frames: List[ActionCameraFrame]) -> TimingMetrics:
    if not frames:
        return TimingMetrics(0.0, 0.0, 0.0, 0.0, 0.0)
    fpss = [f.fps for f in frames]
    fps_mean = sum(fpss) / len(fpss)
    fps_std = statistics.pstdev(fpss) if len(fpss) > 1 else 0.0
    duration = len(frames) / fps_mean if fps_mean > 0 else 0.0
    return TimingMetrics(
        fps_mean=fps_mean,
        fps_min=min(fpss),
        fps_max=max(fpss),
        fps_std=fps_std,
        duration_s=duration,
    )


def compute_route_distribution(frames: List[ActionCameraFrame]) -> Dict[int, int]:
    counts: Counter[int] = Counter()
    for f in frames:
        counts[f.route_type] += 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------
# Per-clip evaluation
# ---------------------------------------------------------------------------


def evaluate_clip(tarball_path: Path, *, run_lint: bool = True) -> ClipReport:
    """Open one tarball / directory, compute every metric, return ClipReport."""
    try:
        with Tarball.from_path(tarball_path) as tar:
            frames = tar.action_camera
            si = tar.systeminfo
            n_depth = len(tar.depth)
            scene_name = None
            operator_id = None
            try:
                gi = tar.gameinfo
                scene_name = gi.get("scene_name") or gi.get("scene")
                operator_id = gi.get("operator_id") or gi.get("operator")
                if scene_name is not None:
                    scene_name = str(scene_name)
                if operator_id is not None:
                    operator_id = str(operator_id)
            except GameDataSDKError:
                # gameinfo.xlsx unreadable — fine, leave None.
                pass

            lint_passed = True
            lint_rate = 1.0
            if run_lint:
                try:
                    lint = tar.validate()
                    lint_passed = lint.passed
                    lint_rate = lint.pass_rate
                except Exception as exc:  # pragma: no cover — defensive
                    logger.warning(f"lint failed for {tarball_path}: {exc}")
                    lint_passed = False
                    lint_rate = 0.0

            return ClipReport(
                clip_id=tarball_path.name.replace(".tar.gz", ""),
                source=str(tarball_path),
                passed_lint=lint_passed,
                lint_pass_rate=lint_rate,
                n_frames=len(frames),
                n_depth=n_depth,
                resolution=(si.width, si.height),
                game=si.game_process_name,
                route_type_distribution=compute_route_distribution(frames),
                trajectory=compute_trajectory(frames),
                actions=compute_actions(frames),
                timing=compute_timing(frames),
                scene_name=scene_name,
                operator_id=operator_id,
            )
    except Exception as exc:
        logger.error(f"evaluate_clip({tarball_path}) failed: {exc}")
        return ClipReport(
            clip_id=tarball_path.name,
            source=str(tarball_path),
            passed_lint=False,
            lint_pass_rate=0.0,
            n_frames=0,
            n_depth=0,
            resolution=(0, 0),
            game="",
            route_type_distribution={},
            trajectory=TrajectoryMetrics(0, 0, 0, 0, 0, 0, 1, 0),
            actions=ActionMetrics({}, 0.0, 0.0, 0.0, None),
            timing=TimingMetrics(0, 0, 0, 0, 0),
            scene_name=None,
            operator_id=None,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Batch evaluation
# ---------------------------------------------------------------------------


def collect_tarballs(batch_dir: Path) -> List[Path]:
    return sorted(p for p in batch_dir.iterdir() if p.suffix in (".gz",) or p.is_dir())


def evaluate_batch(
    tarballs: List[Path],
    *,
    batch_dir: Optional[Path] = None,
    run_lint: bool = True,
) -> BatchReport:
    started = time.time()
    clips: List[ClipReport] = []
    for tp in tarballs:
        logger.info(f"evaluating {tp.name}…")
        clips.append(evaluate_clip(tp, run_lint=run_lint))

    if not clips:
        return BatchReport(
            batch_path=batch_dir,
            n_clips=0,
            n_passed=0,
            overall_route_distribution={},
            overall_scene_distribution={},
            overall_operator_distribution={},
            median_path_length_m=0.0,
            median_n_frames=0,
            median_action_entropy_bits=0.0,
            median_stationary_fraction=0.0,
            median_fps_std=0.0,
            clips=[],
            elapsed_seconds=time.time() - started,
            started_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )

    # Aggregate distributions
    route_counter: Counter[int] = Counter()
    scene_counter: Counter[str] = Counter()
    op_counter: Counter[str] = Counter()
    for c in clips:
        for k, v in c.route_type_distribution.items():
            route_counter[k] += v
        if c.scene_name:
            scene_counter[c.scene_name] += 1
        if c.operator_id:
            op_counter[c.operator_id] += 1

    path_lens = [c.trajectory.path_length_m for c in clips if c.error is None]
    n_frames_list = [c.n_frames for c in clips if c.error is None]
    entropies = [c.actions.key_code_entropy_bits for c in clips if c.error is None]
    statics = [c.trajectory.stationary_fraction for c in clips if c.error is None]
    fps_stds = [c.timing.fps_std for c in clips if c.error is None]

    def _median(xs: List[float]) -> float:
        return statistics.median(xs) if xs else 0.0

    return BatchReport(
        batch_path=batch_dir,
        n_clips=len(clips),
        n_passed=sum(1 for c in clips if c.passed_lint),
        overall_route_distribution=dict(sorted(route_counter.items())),
        overall_scene_distribution=dict(scene_counter),
        overall_operator_distribution=dict(op_counter),
        median_path_length_m=_median(path_lens),
        median_n_frames=int(_median([float(n) for n in n_frames_list])),
        median_action_entropy_bits=_median(entropies),
        median_stationary_fraction=_median(statics),
        median_fps_std=_median(fps_stds),
        clips=clips,
        elapsed_seconds=time.time() - started,
        started_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )


# ---------------------------------------------------------------------------
# HTML report renderer (zero JS dependencies)
# ---------------------------------------------------------------------------


def _bar_chart_svg(items: List[Tuple[str, int]], *, width: int = 480, height: int = 220, color: str = "#2e6fdb") -> str:
    if not items:
        return '<svg width="0" height="0"></svg>'
    max_val = max(v for _, v in items) or 1
    bar_w = max(8, int((width - 60) / len(items)))
    bars: List[str] = []
    for i, (label, value) in enumerate(items):
        bx = 50 + i * (bar_w + 4)
        bh = int((height - 40) * value / max_val)
        by = height - 20 - bh
        bars.append(
            f'<rect x="{bx}" y="{by}" width="{bar_w}" height="{bh}" fill="{color}" />'
            f'<text x="{bx + bar_w/2}" y="{height-6}" font-size="10" text-anchor="middle" '
            f'fill="#333">{html.escape(str(label))}</text>'
            f'<text x="{bx + bar_w/2}" y="{by - 4}" font-size="10" text-anchor="middle" '
            f'fill="#333">{value}</text>'
        )
    return (
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg" '
        f'style="background:#fafafa;border:1px solid #ddd;">'
        + "".join(bars)
        + "</svg>"
    )


def render_html_report(report: BatchReport) -> str:
    """Render the batch report as a standalone, JS-free HTML page."""
    route_items = sorted(report.overall_route_distribution.items(), key=lambda x: x[0])
    route_chart = _bar_chart_svg([(f"type {k}", v) for k, v in route_items], color="#2e6fdb")

    scene_items = sorted(report.overall_scene_distribution.items(), key=lambda x: -x[1])[:20]
    scene_chart = _bar_chart_svg(scene_items, color="#3aa66f")

    op_items = sorted(report.overall_operator_distribution.items(), key=lambda x: -x[1])[:20]
    op_chart = _bar_chart_svg(op_items, color="#d97a2e")

    # Distribution of path lengths
    path_buckets: Counter[str] = Counter()
    for c in report.clips:
        if c.error is not None:
            continue
        m = c.trajectory.path_length_m
        bucket = (
            "0-50m"
            if m < 50
            else "50-200m"
            if m < 200
            else "200-500m"
            if m < 500
            else "500m+"
        )
        path_buckets[bucket] += 1
    path_chart = _bar_chart_svg(
        [(b, path_buckets.get(b, 0)) for b in ("0-50m", "50-200m", "200-500m", "500m+")],
        color="#8b3aa6",
    )

    # Per-clip table
    rows = []
    for c in report.clips:
        rows.append(
            f"<tr>"
            f"<td><code>{html.escape(c.clip_id)}</code></td>"
            f"<td>{c.n_frames}</td>"
            f"<td>{c.n_depth}</td>"
            f"<td>{c.timing.fps_mean:.2f}</td>"
            f"<td>{c.timing.duration_s:.1f}s</td>"
            f"<td>{c.trajectory.path_length_m:.1f}m</td>"
            f"<td>{c.trajectory.stationary_fraction*100:.1f}%</td>"
            f"<td>{c.actions.key_code_entropy_bits:.2f}</td>"
            f"<td>{'PASS' if c.passed_lint else 'FAIL'}</td>"
            f"</tr>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Oyster GameData Buyer Eval Report</title>
  <style>
    body {{ font-family: -apple-system, system-ui, "Segoe UI", sans-serif;
           max-width: 1100px; margin: 24px auto; padding: 0 16px; color: #222; }}
    h1 {{ font-size: 22px; margin-bottom: 4px; }}
    .sub {{ color: #666; margin-bottom: 24px; font-size: 12px; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 24px; }}
    .card {{ border: 1px solid #ddd; padding: 14px; border-radius: 6px; background: #fff; }}
    .stat {{ display: inline-block; padding: 6px 12px; margin: 4px 6px 4px 0;
            background: #eef3fb; border-radius: 4px; font-size: 13px; }}
    .stat strong {{ color: #2e6fdb; }}
    table {{ border-collapse: collapse; font-size: 12px; width: 100%; margin-top: 12px; }}
    th, td {{ border: 1px solid #ddd; padding: 4px 8px; text-align: left; }}
    th {{ background: #f0f0f0; }}
    code {{ font-family: "SF Mono", Menlo, monospace; font-size: 11px; }}
  </style>
</head>
<body>
  <h1>Oyster GameData — Buyer Evaluation Report</h1>
  <div class="sub">
    Batch <code>{html.escape(str(report.batch_path or '—'))}</code> ·
    Generated {html.escape(report.started_at)} ·
    {report.elapsed_seconds:.1f}s elapsed
  </div>

  <div>
    <span class="stat"><strong>{report.n_clips}</strong> clips</span>
    <span class="stat"><strong>{report.n_passed}</strong> passed lint</span>
    <span class="stat">median path <strong>{report.median_path_length_m:.0f}m</strong></span>
    <span class="stat">median frames <strong>{report.median_n_frames}</strong></span>
    <span class="stat">median action entropy <strong>{report.median_action_entropy_bits:.2f} bits</strong></span>
    <span class="stat">median stationary <strong>{report.median_stationary_fraction*100:.1f}%</strong></span>
    <span class="stat">median fps σ <strong>{report.median_fps_std:.2f}</strong></span>
  </div>

  <div class="grid" style="margin-top: 20px;">
    <div class="card">
      <h3>route_type distribution</h3>
      {route_chart}
    </div>
    <div class="card">
      <h3>scene_name distribution (top 20)</h3>
      {scene_chart}
    </div>
    <div class="card">
      <h3>operator_id distribution (top 20)</h3>
      {op_chart}
    </div>
    <div class="card">
      <h3>trajectory path-length buckets</h3>
      {path_chart}
    </div>
  </div>

  <h2 style="margin-top: 28px;">Per-clip details</h2>
  <table>
    <thead>
      <tr>
        <th>clip_id</th><th>n_frames</th><th>n_depth</th>
        <th>fps</th><th>duration</th>
        <th>path</th><th>stationary</th>
        <th>action entropy</th><th>lint</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="buyer_eval_harness",
        description="Run the buyer-spec data-quality evaluation suite on a batch of tarballs.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--batch-dir", type=Path, help="Directory containing *.tar.gz tarballs")
    src.add_argument("--tarball", type=Path, nargs="+", help="Explicit tarball paths")

    p.add_argument("--output", "-o", type=Path, default=Path("eval"),
                   help="Output directory for eval_report.json and eval_report.html")
    p.add_argument("--no-lint", action="store_true", help="Skip the 24-criterion lint pass")
    p.add_argument("--json-only", action="store_true", help="Don't render HTML, only emit JSON")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.batch_dir:
        if not args.batch_dir.is_dir():
            logger.error(f"--batch-dir not a directory: {args.batch_dir}")
            return 2
        tarballs = collect_tarballs(args.batch_dir)
        if not tarballs:
            logger.error(f"no tarballs found in {args.batch_dir}")
            return 2
        batch_root = args.batch_dir
    else:
        tarballs = [Path(p) for p in args.tarball]
        for p in tarballs:
            if not p.exists():
                logger.error(f"not found: {p}")
                return 2
        batch_root = None

    logger.info(f"evaluating {len(tarballs)} clip(s)…")
    report = evaluate_batch(tarballs, batch_dir=batch_root, run_lint=not args.no_lint)
    logger.info(f"done in {report.elapsed_seconds:.1f}s — {report.n_passed}/{report.n_clips} passed lint")

    args.output.mkdir(parents=True, exist_ok=True)
    json_path = args.output / "eval_report.json"
    json_path.write_text(json.dumps(report.to_dict(), indent=2))
    logger.info(f"JSON  → {json_path}")

    if not args.json_only:
        html_path = args.output / "eval_report.html"
        html_path.write_text(render_html_report(report))
        logger.info(f"HTML  → {html_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
