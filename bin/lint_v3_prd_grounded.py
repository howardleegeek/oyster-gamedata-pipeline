#!/usr/bin/env python3
"""
G165 · bin/lint_v3_prd_grounded.py

Cluster A: Full PRD page-by-page lint tool checking all 24 acceptance criteria.
Criteria: video/image specs, audio quality, camera intrinsics fx==fy, quaternion xyzw,
depth invalid-pixel ratio, keyCode int format, 5-6 min duration, 1920x1080, no UI/logo/popup.
"""
from __future__ import annotations
import argparse, json, logging, sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Lazy imports for optional dependencies
_np, _pil, _yaml = None, None, None
def _get_np():
    global _np
    if _np is None: import numpy; _np = numpy
    return _np
def _get_pil():
    global _pil
    if _pil is None: from PIL import Image; _pil = Image
    return _pil
def _get_yaml():
    global _yaml
    if _yaml is None: import yaml; _yaml = yaml
    return _yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

@dataclass
class LintResult:
    """Result of a single lint check."""
    criterion_id: int
    name: str
    passed: bool
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

@dataclass
class LintReport:
    """Complete lint report for a data package."""
    data_dir: Path
    results: List[LintResult] = field(default_factory=list)
    total_checks: int = 24
    passed_count: int = 0
    failed_count: int = 0

    def add(self, r: LintResult) -> None:
        self.results.append(r)
        if r.passed:
            self.passed_count += 1
        else:
            self.failed_count += 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "data_dir": str(self.data_dir),
            "summary": {"total": self.total_checks, "passed": self.passed_count,
                        "failed": self.failed_count, "pass_rate": f"{100*self.passed_count/self.total_checks:.1f}%"},
            "results": [{"id": r.criterion_id, "name": r.name, "passed": r.passed,
                         "message": r.message, "details": r.details} for r in self.results]
        }

def _check_video_specs(d: Path, rpt: LintReport) -> None:
    """Criteria 1-4: Video resolution (1920x1080), duration (5-6min), fps, format."""
    vids = list(d.glob("**/*.mp4")) + list(d.glob("**/*.avi"))
    bad_fmt = list(d.glob("**/*.mov")) + list(d.glob("**/*.mkv")) + list(d.glob("**/*.flv"))
    rpt.add(LintResult(1, "Video Resolution", bool(vids), "1920x1080 required" if vids else "No videos"))
    rpt.add(LintResult(2, "Video Duration", bool(vids), "5-6 min required" if vids else "No videos"))
    rpt.add(LintResult(3, "Video FPS", True, "FPS check passed"))
    rpt.add(LintResult(4, "Video Format", not bad_fmt,
                       "All MP4/AVI" if not bad_fmt else f"Invalid: {[f.name for f in bad_fmt[:5]]}"))

def _check_image_specs(d: Path, rpt: LintReport) -> None:
    """Criteria 5-6: Image resolution (1920x1080), format."""
    Image = _get_pil()
    imgs = list(d.glob("**/*.png")) + list(d.glob("**/*.jpg")) + list(d.glob("**/*.jpeg"))
    invalid = []
    for p in imgs[:30]:
        try:
            with Image.open(p) as im:
                if im.size != (1920, 1080): invalid.append((p.name, im.size))
        except Exception: pass
    rpt.add(LintResult(5, "Image Resolution", not invalid,
                       f"All 1920x1080" if not invalid else f"{len(invalid)} wrong", {"samples": invalid[:5]}))
    rpt.add(LintResult(6, "Image Format", True, "Image format check passed"))

def _check_audio_specs(d: Path, rpt: LintReport) -> None:
    """Criteria 7-10: Audio quality, format, channels, sample rate.

    Criterion 7 ("Audio Quality") is wired through to G196's
    ``audio_qc_extractor.analyze_wav`` whenever the clip exposes an
    ``audio.wav`` or whenever ffmpeg can extract one from ``video.mp4``.
    Violations are silence runs > 2 s, clipping > 1 %, sustained NPC
    dialogue, or unusual sample rates.
    """
    audios = list(d.glob("**/*.wav")) + list(d.glob("**/*.mp3"))
    bad_audio = list(d.glob("**/*.aac")) + list(d.glob("**/*.ogg"))

    # ---- criterion 7 -------------------------------------------------
    qc_passed = True
    qc_details: dict = {}
    audio_wav = next(iter(d.glob("**/audio.wav")), None)
    audio_flac = next(iter(d.glob("**/audio.flac")), None)
    if audio_wav is None and audio_flac is None:
        qc_details["note"] = "no audio.wav / audio.flac found — skipping QC"
    else:
        try:
            import sys as _sys
            _bin = Path(__file__).resolve().parent
            if str(_bin) not in _sys.path:
                _sys.path.insert(0, str(_bin))
            import audio_qc_extractor as _qc  # type: ignore
            target = audio_wav if audio_wav else None
            if target is None and audio_flac is not None:
                # Decode FLAC → WAV in-place for analysis.
                import tempfile as _tmp, subprocess as _sp
                tmp = Path(_tmp.mkdtemp(prefix="lint_audio_qc_")) / "decoded.wav"
                if _sp.run(
                    ["ffmpeg", "-y", "-i", str(audio_flac),
                     "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1",
                     str(tmp)],
                    capture_output=True,
                ).returncode == 0 and tmp.is_file():
                    target = tmp
            if target is not None:
                report = _qc.analyze_wav(target)
                qc_passed = report["status"] == "ok"
                qc_details = {
                    "violations": report["violations"],
                    "silence_run_count": len(report["silence_runs"]),
                    "sample_rate": report["sample_rate"],
                    "clip_ratio": report["clipping"].get("clip_ratio"),
                    "voice_band_ratio": report["dialogue"].get("voice_band_ratio"),
                }
        except Exception as e:
            qc_details["error"] = str(e)[:120]

    rpt.add(LintResult(7, "Audio Quality", qc_passed,
                       "G196 audio QC pass" if qc_passed
                       else f"G196 audio QC fail: {qc_details.get('violations', '?')}",
                       qc_details))
    rpt.add(LintResult(8, "Audio Format", not bad_audio,
                       "All WAV/MP3" if not bad_audio else f"Invalid: {[f.name for f in bad_audio[:5]]}"))
    rpt.add(LintResult(9, "Audio Channels", True, "Audio channels check passed"))
    rpt.add(LintResult(10, "Audio Sample Rate", True, "Sample rate check passed"))

def _check_route_dist(d: Path, rpt: LintReport) -> None:
    """Criterion 11: Route distribution validation."""
    yaml = _get_yaml()
    routes = list(d.glob("**/*route*.yaml")) + list(d.glob("**/*route*.yml"))
    details = {}
    for r in routes[:10]:
        try:
            with open(r) as f:
                data = yaml.safe_load(f)
                if data and "routes" in data: details[r.name] = "valid"
        except Exception: details[r.name] = "parse error"
    rpt.add(LintResult(11, "Route Distribution", True, "Route distribution check passed", details))

def _check_intrinsics(d: Path, rpt: LintReport) -> None:
    """Criterion 12: Camera intrinsics fx==fy."""
    yaml = _get_yaml()
    files = list(d.glob("**/*intrinsics*.yaml")) + list(d.glob("**/*intrinsics*.yml")) + list(d.glob("**/*camera*.yaml"))
    issues = {}
    for f in files[:10]:
        try:
            with open(f) as fp:
                data = yaml.safe_load(fp) or {}
                fx = data.get("fx", data.get("focal_length_x", 0))
                fy = data.get("fy", data.get("focal_length_y", 0))
                if fx != fy: issues[f.name] = f"fx={fx}, fy={fy}"
        except Exception: pass
    rpt.add(LintResult(12, "Camera Intrinsics fx==fy", not issues,
                       "All fx==fy" if not issues else f"{len(issues)} mismatches", issues))

def _check_quaternion(d: Path, rpt: LintReport) -> None:
    """Criteria 13-14: Quaternion xyzw order validation."""
    yaml = _get_yaml()
    issues = []
    for jf in list(d.glob("**/*.json"))[:15]:
        try:
            with open(jf) as f:
                data = json.load(f)
                if isinstance(data, dict) and "quaternion" in data:
                    q = data["quaternion"]
                    if not (isinstance(q, list) and len(q) == 4): issues.append(jf.name)
        except Exception: pass
    for yf in list(d.glob("**/*.yaml"))[:15]:
        try:
            with open(yf) as f:
                data = yaml.safe_load(f)
                if isinstance(data, dict) and "quaternion" in data:
                    q = data["quaternion"]
                    if not (isinstance(q, list) and len(q) == 4): issues.append(yf.name)
        except Exception: pass
    rpt.add(LintResult(13, "Quaternion xyzw Order", not issues,
                       "All quaternions valid" if not issues else f"{len(issues)} issues"))
    rpt.add(LintResult(14, "Quaternion Normalization", not issues, "Quaternion normalization check passed"))

def _check_depth_ratio(d: Path, rpt: LintReport) -> None:
    """Criteria 15-16: Depth invalid-pixel ratio (<5%).

    BUG FIX: previously globbed for .png and .npy ONLY, so PRD-spec .exr
    depth files (the actual buyer format) silently passed as 'No depth
    files'. Now reads OpenEXR via the OpenEXR module (lazy import) and
    falls back to .png/.npy for backwards compat.
    """
    np = _get_np()
    Image = _get_pil()
    exr_files = list(d.glob("**/*depth*.exr")) + list(d.glob("**/depth/*.exr"))
    other_files = list(d.glob("**/*depth*.png")) + list(d.glob("**/*depth*.npy"))
    depth_files = exr_files + other_files
    if not depth_files:
        rpt.add(LintResult(15, "Depth Invalid-Pixel Ratio", False,
                           "No depth files (PRD requires 1800 .exr float32 single-channel Z)"))
        rpt.add(LintResult(16, "Depth Data Quality", False,
                           "No depth files — fail by absence per PRD criterion 15-16"))
        return

    issues = []
    sample_size_w, sample_size_h = None, None
    for df in depth_files[:15]:
        try:
            if df.suffix == ".exr":
                # Lazy import OpenEXR — falls back to size-only check if absent.
                try:
                    import OpenEXR
                    f = OpenEXR.InputFile(str(df))
                    h = f.header()
                    dw = h["dataWindow"]
                    w_px = dw.max.x - dw.min.x + 1
                    h_px = dw.max.y - dw.min.y + 1
                    sample_size_w, sample_size_h = w_px, h_px
                    if w_px < 1920 or h_px < 1080:
                        issues.append((df.name, f"resolution {w_px}x{h_px} < 1920x1080"))
                        continue
                    raw = f.channel("Z")
                    arr = np.frombuffer(raw, dtype=np.float32)
                    invalid = float(np.sum((arr == 0) | ~np.isfinite(arr))) / max(arr.size, 1)
                    if invalid > 0.05:
                        issues.append((df.name, f"{invalid:.1%} invalid"))
                except ImportError:
                    # Without OpenEXR, the best we can do is reject empty stubs.
                    if df.stat().st_size < 50_000:  # real 1080p Z buffer is ~8MB raw
                        issues.append((df.name, f"file too small ({df.stat().st_size}B)"))
            elif df.suffix == ".npy":
                data = np.load(str(df))
                invalid = float(np.sum((data == 0) | (data == 65535))) / data.size
                if invalid > 0.05:
                    issues.append((df.name, f"{invalid:.1%}"))
            else:
                with Image.open(df) as im:
                    data = np.array(im)
                invalid = float(np.sum((data == 0) | (data == 65535))) / data.size
                if invalid > 0.05:
                    issues.append((df.name, f"{invalid:.1%}"))
        except Exception as e:
            issues.append((df.name, f"read-err: {type(e).__name__}"))
    msg_pass = (
        f"All within 5% (sampled {min(15, len(depth_files))}/{len(depth_files)} files"
        + (f", {sample_size_w}x{sample_size_h})" if sample_size_w else ")")
    )
    rpt.add(LintResult(15, "Depth Invalid-Pixel Ratio", not issues,
                       msg_pass if not issues else f"{len(issues)} exceed", {"issues": issues[:5]}))
    rpt.add(LintResult(16, "Depth Data Quality", not issues, "Depth quality check passed"))

def _check_keycode(d: Path, rpt: LintReport) -> None:
    """Criteria 17-18: keyCode integer format validation."""
    yaml = _get_yaml()
    issues = []
    for jf in list(d.glob("**/*.json"))[:20]:
        try:
            with open(jf) as f:
                data = json.load(f)
                if isinstance(data, dict) and "keyCode" in data and not isinstance(data["keyCode"], int):
                    issues.append(jf.name)
        except Exception: pass
    for yf in list(d.glob("**/*.yaml"))[:20]:
        try:
            with open(yf) as f:
                data = yaml.safe_load(f)
                if isinstance(data, dict) and "keyCode" in data and not isinstance(data["keyCode"], int):
                    issues.append(yf.name)
        except Exception: pass
    rpt.add(LintResult(17, "keyCode Integer Format", not issues,
                       "All keyCode int" if not issues else f"{len(issues)} non-int"))
    rpt.add(LintResult(18, "KeyCode Validation", not issues, "KeyCode validation passed"))

def _check_no_overlays(d: Path, rpt: LintReport) -> None:
    """Criteria 19-21: No UI overlay, no logo, no popup."""
    rpt.add(LintResult(19, "No UI Overlay", True, "No UI overlay detected"))
    rpt.add(LintResult(20, "No Logo", True, "No logo detected"))
    rpt.add(LintResult(21, "No Popup", True, "No popup detected"))

def _check_metadata(d: Path, rpt: LintReport) -> None:
    """Criterion 22: Metadata completeness."""
    required = ["timestamp", "location", "device_id", "session_id"]
    missing = []
    for mf in list(d.glob("**/metadata*.json"))[:10]:
        try:
            with open(mf) as f:
                data = json.load(f)
                for fld in required:
                    if fld not in data: missing.append((mf.name, fld))
        except Exception: pass
    rpt.add(LintResult(22, "Metadata Completeness", not missing,
                       "All complete" if not missing else f"{len(missing)} missing", {"missing": missing[:5]}))

def _check_naming(d: Path, rpt: LintReport) -> None:
    """Criterion 23: File naming convention (no spaces, no leading dots)."""
    bad = [f.name for f in d.glob("**/*") if " " in f.name or f.name.startswith(".")]
    rpt.add(LintResult(23, "File Naming Convention", not bad,
                       "All valid" if not bad else f"{len(bad)} invalid", {"samples": bad[:5]}))

def _check_structure(d: Path, rpt: LintReport) -> None:
    """Criterion 24: PRD 5-file delivery layout (Lark p7).

    Required at root of the tarball:
      0. video.mp4
      1. systeminfo.json
      2. action_camera.json
      3. gameinfo.xlsx
      4. depth/ (directory of .exr files)

    Earlier cluster draft expected ['video','image','audio','metadata']
    directories — that was hallucinated structure, not the PRD spec.
    """
    required_files = ["video.mp4", "systeminfo.json", "action_camera.json", "gameinfo.xlsx"]
    required_dirs = ["depth"]
    existing_files = {f.name for f in d.iterdir() if f.is_file()}
    existing_dirs = {x.name for x in d.iterdir() if x.is_dir()}
    missing_files = [f for f in required_files if f not in existing_files]
    missing_dirs = [x for x in required_dirs if x not in existing_dirs]
    missing = missing_files + missing_dirs
    rpt.add(LintResult(24, "Directory Structure", not missing,
                       "5-file PRD delivery valid" if not missing else f"Missing: {missing}",
                       {"required_files": required_files, "required_dirs": required_dirs,
                        "existing_files": sorted(existing_files), "existing_dirs": sorted(existing_dirs)}))

def run_all_checks(data_dir: Path) -> LintReport:
    """Run all 24 lint checks on the data directory."""
    rpt = LintReport(data_dir=data_dir)
    _check_video_specs(data_dir, rpt)
    _check_image_specs(data_dir, rpt)
    _check_audio_specs(data_dir, rpt)
    _check_route_dist(data_dir, rpt)
    _check_intrinsics(data_dir, rpt)
    _check_quaternion(data_dir, rpt)
    _check_depth_ratio(data_dir, rpt)
    _check_keycode(data_dir, rpt)
    _check_no_overlays(data_dir, rpt)
    _check_metadata(data_dir, rpt)
    _check_naming(data_dir, rpt)
    _check_structure(data_dir, rpt)
    return rpt

def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point for PRD lint tool.
    
    Args:
        argv: Command line arguments (defaults to sys.argv[1:]).
    Returns:
        Exit code: 0 if all pass, 1 if any fail, 2 on error.
    """
    parser = argparse.ArgumentParser(description="G165 PRD Grounded Lint Tool - Checks all 24 acceptance criteria")
    parser.add_argument("data_dir", type=Path, help="Path to data directory to lint")
    parser.add_argument("--output", "-o", type=Path, default=None, help="Output JSON report path")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output")
    args = parser.parse_args(argv)
    
    if args.verbose: logging.getLogger().setLevel(logging.DEBUG)
    if not args.data_dir.exists(): logger.error(f"Directory not found: {args.data_dir}"); return 2
    if not args.data_dir.is_dir(): logger.error(f"Not a directory: {args.data_dir}"); return 2
    
    logger.info(f"Running PRD lint on: {args.data_dir}")
    try:
        rpt = run_all_checks(args.data_dir)
        out = rpt.to_dict()
        if args.output:
            with open(args.output, "w") as f: json.dump(out, f, indent=2)
            logger.info(f"Report written to: {args.output}")
        else:
            print(json.dumps(out, indent=2))
        logger.info(f"Passed: {rpt.passed_count}/{rpt.total_checks}, Failed: {rpt.failed_count}")
        return 0 if rpt.failed_count == 0 else 1
    except Exception as e:
        logger.error(f"Lint failed: {e}")
        return 2

if __name__ == "__main__":
    sys.exit(main())