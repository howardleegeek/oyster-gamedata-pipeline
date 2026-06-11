#!/usr/bin/env python3
"""
multi_clip_stitcher.py — Combine N short same-scene clips into a longer episode.

Preserves timestamp continuity and frame_id offset across clips so that buyers
receive a single coherent trajectory with monotonically increasing timestamps
and frame identifiers.

Usage:
    python3 bin/multi_clip_stitcher.py --clips clip_001 clip_002 --output stitched
    python3 bin/multi_clip_stitcher.py --manifest clips.yaml --output stitched
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SUPPORTED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"}
METADATA_FILENAME = "metadata.json"
ANNOTATIONS_FILENAME = "annotations.json"
STITCH_MANIFEST_FILENAME = "stitch_manifest.json"


def _natural_sort_key(path: Path) -> Tuple[int, str]:
    """Sort key handling numeric prefixes in filenames."""
    name = path.stem
    digits = ""
    for ch in name:
        if ch.isdigit():
            digits += ch
        else:
            break
    return (int(digits) if digits else 0, name)


def _collect_frames(clip_dir: Path) -> List[Path]:
    """Collect and sort frame image files from a clip directory."""
    frames_dir = clip_dir / "frames"
    src = frames_dir if frames_dir.is_dir() else clip_dir
    frames = [f for f in src.iterdir() if f.is_file() and f.suffix.lower() in SUPPORTED_IMAGE_EXTS]
    frames.sort(key=_natural_sort_key)
    return frames


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    """Load a JSON file; return None if missing."""
    if not path.is_file():
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _save_json(path: Path, data: Dict[str, Any]) -> None:
    """Write data to a JSON file with pretty formatting."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _extract_list(metadata: Dict[str, Any], key: str, cast: type) -> List[Any]:
    """Extract a list from metadata, supporting 'frames' list or flat key."""
    frames_info = metadata.get("frames", [])
    if frames_info and isinstance(frames_info, list):
        return [f.get(key, i) for i, f in enumerate(frames_info)]
    val = metadata.get(key + "s", [])
    if isinstance(val, list):
        return [cast(v) for v in val]
    return []


def _compute_offsets(
    prev_end_ts: float, prev_end_fid: int,
    clip_ts: List[float], clip_fids: List[int],
) -> Tuple[float, int]:
    """Compute timestamp and frame_id offsets for monotonic continuity."""
    if not clip_ts:
        return (prev_end_ts + 1.0, prev_end_fid + 1)
    return (prev_end_ts - clip_ts[0] + 1.0, prev_end_fid - clip_fids[0] + 1)


def _adjust_metadata(metadata: Dict[str, Any], ts_off: float, fid_off: int) -> Dict[str, Any]:
    """Return deep-copied metadata with timestamps and frame_ids shifted."""
    adj = copy.deepcopy(metadata)
    frames_info = adj.get("frames", [])
    if frames_info and isinstance(frames_info, list):
        for frame in frames_info:
            if "timestamp" in frame:
                frame["timestamp"] = round(frame["timestamp"] + ts_off, 6)
            if "frame_id" in frame:
                frame["frame_id"] = frame["frame_id"] + fid_off
    else:
        if "timestamps" in adj:
            adj["timestamps"] = [round(t + ts_off, 6) for t in adj["timestamps"]]
        if "frame_ids" in adj:
            adj["frame_ids"] = [f + fid_off for f in adj["frame_ids"]]
    return adj


def _adjust_annotations(annotations: Dict[str, Any], fid_off: int) -> Dict[str, Any]:
    """Shift frame_id references inside annotations."""
    adj = copy.deepcopy(annotations)
    frames_map = adj.get("frames", {})
    if isinstance(frames_map, dict):
        adj["frames"] = {
            str(int(k) + fid_off) if k.isdigit() else k: v
            for k, v in frames_map.items()
        }
    if isinstance(adj.get("annotations"), list):
        for ann in adj["annotations"]:
            if "frame_id" in ann:
                ann["frame_id"] = ann["frame_id"] + fid_off
    return adj


def stitch_clips(
    clip_dirs: List[Path],
    output_dir: Path,
    *,
    copy_frames: bool = True,
) -> Dict[str, Any]:
    """
    Stitch multiple clip directories into a single output directory.

    Parameters
    ----------
    clip_dirs : list[Path]
        Ordered list of clip directories to combine.
    output_dir : Path
        Destination directory for the stitched episode.
    copy_frames : bool
        If True, copy frame images into output/frames/.

    Returns
    -------
    dict — Stitch manifest describing the operation.
    """
    if len(clip_dirs) < 2:
        raise ValueError("At least 2 clips are required for stitching.")

    output_dir.mkdir(parents=True, exist_ok=True)
    out_frames = output_dir / "frames"
    if copy_frames:
        out_frames.mkdir(parents=True, exist_ok=True)

    all_ts: List[float] = []
    all_fids: List[int] = []
    all_frames_meta: List[Dict[str, Any]] = []
    merged_ann: Optional[Dict[str, Any]] = None
    entries: List[Dict[str, Any]] = []
    prev_ts, prev_fid = 0.0, -1

    for idx, clip_dir in enumerate(clip_dirs):
        if not clip_dir.is_dir():
            raise FileNotFoundError(f"Clip directory not found: {clip_dir}")
        logger.info("Processing clip %d/%d: %s", idx + 1, len(clip_dirs), clip_dir)

        meta = _load_json(clip_dir / METADATA_FILENAME) or {"frames": []}
        clip_ts = _extract_list(meta, "timestamp", float)
        clip_fids = _extract_list(meta, "frame_id", int)
        frames = _collect_frames(clip_dir)
        n = len(clip_ts) or len(clip_fids) or len(frames)
        if not clip_ts:
            clip_ts = [float(i) for i in range(n)]
            clip_fids = list(range(n))

        ts_off, fid_off = _compute_offsets(prev_ts, prev_fid, clip_ts, clip_fids)
        adj = _adjust_metadata(meta, ts_off, fid_off)
        a_ts = _extract_list(adj, "timestamp", float)
        a_fids = _extract_list(adj, "frame_id", int)
        all_ts.extend(a_ts)
        all_fids.extend(a_fids)
        fi = adj.get("frames", [])
        if fi:
            all_frames_meta.extend(fi)

        if copy_frames:
            for fp in frames:
                shutil.copy2(fp, out_frames / f"c{idx:04d}_{fp.name}")

        ann = _load_json(clip_dir / ANNOTATIONS_FILENAME)
        if ann is not None:
            a_ann = _adjust_annotations(ann, fid_off)
            if merged_ann is None:
                merged_ann = {"frames": {}, "annotations": []}
            sf = a_ann.get("frames", {})
            if isinstance(sf, dict):
                merged_ann["frames"].update(sf)
            sa = a_ann.get("annotations", [])
            if isinstance(sa, list):
                merged_ann["annotations"].extend(sa)

        entries.append({
            "clip_index": idx, "clip_path": str(clip_dir), "num_frames": n,
            "timestamp_offset": round(ts_off, 6), "frame_id_offset": fid_off,
            "first_timestamp": a_ts[0] if a_ts else None,
            "last_timestamp": a_ts[-1] if a_ts else None,
            "first_frame_id": a_fids[0] if a_fids else None,
            "last_frame_id": a_fids[-1] if a_fids else None,
        })
        if a_ts:
            prev_ts = a_ts[-1]
        if a_fids:
            prev_fid = a_fids[-1]

    merged_meta: Dict[str, Any] = {
        "num_clips": len(clip_dirs), "total_frames": len(all_ts),
        "timestamps": all_ts, "frame_ids": all_fids,
    }
    if all_frames_meta:
        merged_meta["frames"] = all_frames_meta
    if meta:
        for k in ("scene_id", "camera_id", "resolution", "fps", "dataset"):
            if k in meta and k not in merged_meta:
                merged_meta[k] = meta[k]
    _save_json(output_dir / METADATA_FILENAME, merged_meta)

    if merged_ann is not None:
        _save_json(output_dir / ANNOTATIONS_FILENAME, merged_ann)

    manifest: Dict[str, Any] = {
        "tool": "multi_clip_stitcher", "version": "1.0.0",
        "num_clips": len(clip_dirs), "total_frames": len(all_ts),
        "output_dir": str(output_dir), "clips": entries,
        "timestamp_range": [all_ts[0] if all_ts else None, all_ts[-1] if all_ts else None],
        "frame_id_range": [all_fids[0] if all_fids else None, all_fids[-1] if all_fids else None],
    }
    _save_json(output_dir / STITCH_MANIFEST_FILENAME, manifest)
    logger.info("Stitched %d clips → %d frames in %s", len(clip_dirs), len(all_ts), output_dir)
    return manifest


def _load_manifest(manifest_path: Path) -> List[Path]:
    """Load a YAML or JSON manifest listing clip directories."""
    text = manifest_path.read_text(encoding="utf-8")
    if manifest_path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # lazy import
            data = yaml.safe_load(text)
        except ImportError:
            logger.error("PyYAML not installed; cannot parse YAML manifest.")
            raise
    else:
        data = json.loads(text)
    if not isinstance(data, dict) or "clips" not in data:
        raise ValueError(f"Manifest must contain a 'clips' key: {manifest_path}")
    return [Path(c) for c in data["clips"]]


# ---------------------------------------------------------------------------
# G197 — scene-continuity (≤ 30 min) + duplicate-clip fraud detection.
#
# Operates over a flat list of *clip metadata* dicts. Each entry should
# expose ``clip_id``, ``operator_id``, ``scene_id``, ``duration_s``, and
# optionally ``content_hash`` and ``start_time``.
# ---------------------------------------------------------------------------

SCENE_DURATION_CAP_S = 30 * 60        # PRD §3.1 — "同一地图场景 ≤ 30 分钟"


def _group_by_operator_scene(metas: List[Dict[str, Any]]) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    """Bucket clip metadata by ``(operator_id, scene_id)`` pairs."""
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for m in metas:
        key = (str(m.get("operator_id", "?")), str(m.get("scene_id", "?")))
        groups.setdefault(key, []).append(m)
    return groups


def analyze_scene_continuity(
    metas: List[Dict[str, Any]],
    cap_seconds: float = SCENE_DURATION_CAP_S,
) -> Dict[str, Any]:
    """Compute the aggregate duration per (operator, scene) and flag any
    bucket that exceeds the PRD §3.1 scene cap.

    Returns a dict with::

        {
          "total_duration_s": float,
          "scene_minute_cap_violation": bool,
          "cap_seconds": int,
          "groups": [
            {
              "operator_id": "...", "scene_id": "...",
              "clip_ids": [...], "duration_s": float, "violation": bool,
            },
            ...
          ]
        }
    """
    groups: List[Dict[str, Any]] = []
    total = 0.0
    any_violation = False
    for (op, scene), clips in sorted(_group_by_operator_scene(metas).items()):
        dur = sum(float(c.get("duration_s", 0.0)) for c in clips)
        total += dur
        violation = dur > cap_seconds
        any_violation = any_violation or violation
        groups.append({
            "operator_id": op,
            "scene_id": scene,
            "clip_ids": [c.get("clip_id", "?") for c in clips],
            "clip_count": len(clips),
            "duration_s": round(dur, 3),
            "violation": violation,
        })
    return {
        "cap_seconds": cap_seconds,
        "total_duration_s": round(total, 3),
        "scene_minute_cap_violation": any_violation,
        "groups": groups,
    }


def detect_duplicate_clips(metas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flag clips that share the same ``content_hash`` AND the same
    ``start_time`` — strong fraud signal (re-submitted recording).

    Records with a missing or falsy hash / start are ignored (we can't
    claim duplicates without both keys).
    """
    buckets: Dict[Tuple[str, float], List[str]] = {}
    for m in metas:
        h = m.get("content_hash")
        st = m.get("start_time")
        if not h or st is None:
            continue
        try:
            st_f = float(st)
        except (TypeError, ValueError):
            continue
        # Round start_time to 3 decimals so float drift doesn't break grouping.
        buckets.setdefault((str(h), round(st_f, 3)), []).append(
            str(m.get("clip_id", "?"))
        )
    duplicates: List[Dict[str, Any]] = []
    for (hash_val, start), ids in buckets.items():
        if len(ids) >= 2:
            duplicates.append({
                "content_hash": hash_val,
                "start_time": start,
                "clip_ids": sorted(ids),
                "count": len(ids),
            })
    return duplicates


def run_continuity_report(
    metas: List[Dict[str, Any]],
    output_path: Path,
    cap_seconds: float = SCENE_DURATION_CAP_S,
) -> int:
    """Compute continuity + duplicate analysis and write
    ``scene_continuity_report.json``. Returns 0 when clean, 1 when any
    violation or duplicate is flagged (CI-friendly)."""
    continuity = analyze_scene_continuity(metas, cap_seconds=cap_seconds)
    dupes = detect_duplicate_clips(metas)
    payload = {
        "tool": "multi_clip_stitcher",
        "mode": "continuity",
        "cap_seconds": continuity["cap_seconds"],
        "total_duration_s": continuity["total_duration_s"],
        "scene_minute_cap_violation": continuity["scene_minute_cap_violation"],
        "groups": continuity["groups"],
        "duplicates": dupes,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _save_json(output_path, payload)
    return 1 if (continuity["scene_minute_cap_violation"] or dupes) else 0


def _load_meta_file(path: Path) -> List[Dict[str, Any]]:
    """Load clip metadata from a flat JSON list, a {'clips':[...]} dict,
    or a directory of per-clip JSON files."""
    if path.is_dir():
        metas: List[Dict[str, Any]] = []
        for fp in sorted(path.glob("*.json")):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("skipping %s: %s", fp, exc)
                continue
            if isinstance(data, list):
                metas.extend(data)
            elif isinstance(data, dict):
                if "clips" in data and isinstance(data["clips"], list):
                    metas.extend(data["clips"])
                else:
                    data.setdefault("clip_id", fp.stem)
                    metas.append(data)
        return metas
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and "clips" in payload:
        return payload["clips"]
    if isinstance(payload, dict):
        return [payload]
    raise ValueError(f"unrecognised metadata payload in {path}")


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser."""
    p = argparse.ArgumentParser(
        prog="multi_clip_stitcher",
        description="Combine N short same-scene clips into a longer episode, "
                    "or run G197 scene-continuity / duplicate analysis.",
    )
    p.add_argument("--clips", nargs="+", type=Path,
                   help="Ordered clip directories (stitch mode).")
    p.add_argument("--manifest", type=Path,
                   help="YAML/JSON manifest of clip dirs (stitch mode).")
    p.add_argument("--output", "-o", type=Path,
                   help="Output path (directory for stitch, JSON file for "
                        "continuity).")
    p.add_argument("--no-copy-frames", action="store_true",
                   help="Stitch mode: metadata-only dry run.")
    p.add_argument("--continuity", action="store_true",
                   help="Run G197 scene-continuity + duplicate analysis.")
    p.add_argument("--metadata", type=Path,
                   help="Continuity mode: JSON list / dir of clip metadata.")
    p.add_argument("--cap-seconds", type=float, default=SCENE_DURATION_CAP_S,
                   help="Per-scene duration cap (default: 1800 s = 30 min).")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Verbose logging.")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point — dispatches between stitch and continuity modes."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # ---- continuity mode --------------------------------------------------
    if args.continuity:
        if args.metadata is None:
            logger.error("--continuity requires --metadata <path>")
            return 1
        try:
            metas = _load_meta_file(args.metadata)
        except Exception as exc:
            logger.error("failed to load metadata: %s", exc)
            return 1
        out = args.output or Path("scene_continuity_report.json")
        return run_continuity_report(metas, out, cap_seconds=args.cap_seconds)

    # ---- stitch mode ------------------------------------------------------
    if args.clips is None and args.manifest is None:
        logger.error("provide --clips, --manifest, or --continuity")
        return 1
    if args.output is None:
        logger.error("stitch mode requires --output <dir>")
        return 1
    clip_dirs = (_load_manifest(args.manifest) if args.manifest
                 else [p.resolve() for p in args.clips])
    for cd in clip_dirs:
        if not cd.is_dir():
            logger.error("Clip directory does not exist: %s", cd)
            return 1
    out = args.output.resolve()
    if out.exists() and any(out.iterdir()):
        logger.warning("Output directory %s is not empty.", out)
    try:
        stitch_clips(clip_dirs, out, copy_frames=not args.no_copy_frames)
    except Exception as exc:
        logger.error("Stitching failed: %s", exc)
        return 1
    logger.info("Done. Stitched episode written to %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
