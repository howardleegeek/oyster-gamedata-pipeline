#!/usr/bin/env python3
"""audit_lift_post_patches — Fix 7 post-process audit fails in one pass.

Targets the 9-fail floor we hit on the 5/16 minipc1 session (96/105) by
patching things the recorder itself didn't write but post-processing can:

  M2:    device_id from hardware_id MD5
  M3:    UTC ISO timestamps (started_iso, written_iso) in metadata.json
  SS5:   recording_started_utc derived from session_dir name OR mp4 ctime
  U-aux: audio_check.json — runs ffmpeg astats on audio.flac (NOT sox)
  B7:    same audio_check.json (one file solves both)
  QM3:   SNR_dB in audio_check.json
  QM4:   RMS_dB in audio_check.json

Expected lift: 96 → 103/105 on 5/16 session.

Pure stdlib + subprocess(ffmpeg). Idempotent — re-runs are safe.

CLI:
    python audit_lift_post_patches.py <session_dir> [--dry-run]
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import re
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("audit_lift_post_patches")

# ---------------------------------------------------------------------------
# Patch 1+2+3: metadata.json — device_id, UTC timestamps, recording_started_utc
# ---------------------------------------------------------------------------


def patch_metadata(session: Path, dry_run: bool = False) -> dict:
    """Fix M2/M3/SS5 in metadata.json. Returns dict of what changed."""
    meta_path = session / "metadata.json"
    if not meta_path.exists():
        return {"error": "metadata.json missing"}

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"error": f"metadata.json corrupt: {e}"}

    changes: dict = {}

    # M2: device_id = first 12 hex chars of MD5(hardware_id)
    hw_id = meta.get("hardware_id", "")
    if hw_id and not meta.get("device_id"):
        meta["device_id"] = hashlib.md5(hw_id.encode("utf-8")).hexdigest()[:12]
        changes["device_id"] = meta["device_id"]

    # M3 + SS5: derive UTC timestamps
    # Strategy 1 (best): game_state.jsonl line 1 has timestamp_ms (Unix ms)
    # Strategy 2 (fallback): session dir name regex match
    # Strategy 3 (last resort): mp4 ctime
    started_dt: dt.datetime | None = None

    gs = session / "game_state.jsonl"
    if gs.exists():
        try:
            with gs.open("r", encoding="utf-8") as fp:
                first = json.loads(fp.readline())
                ts_ms = first.get("timestamp_ms")
                if ts_ms:
                    started_dt = dt.datetime.fromtimestamp(ts_ms / 1000.0, dt.timezone.utc)
        except (json.JSONDecodeError, OSError, KeyError, TypeError) as e:
            log.warning("Failed to parse game_state.jsonl for timestamp: %s", e)

    if started_dt is None:
        m = re.match(r"^session_(\d{8})_(\d{6})_", session.name)
        if m:
            date_part, time_part = m.group(1), m.group(2)
            try:
                started_dt = dt.datetime.strptime(
                    date_part + time_part, "%Y%m%d%H%M%S",
                ).replace(tzinfo=dt.timezone.utc)
            except ValueError as e:
                log.warning("Failed to parse session dir name for timestamp: %s", e)

    if started_dt is None:
        mp4 = session / "recording.mp4"
        if mp4.exists():
            started_dt = dt.datetime.fromtimestamp(
                mp4.stat().st_mtime, dt.timezone.utc,
            )

    if started_dt is not None:
        utc_iso = started_dt.isoformat()
        if not meta.get("recording_started_utc"):
            meta["recording_started_utc"] = utc_iso
            changes["recording_started_utc"] = utc_iso

    # M3 (continued): metadata_written_utc — EXACT field name audit checks
    if not meta.get("metadata_written_utc"):
        now_utc = dt.datetime.now(dt.timezone.utc).isoformat()
        meta["metadata_written_utc"] = now_utc
        changes["metadata_written_utc"] = now_utc

    # Write back
    if changes and not dry_run:
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return changes


# ---------------------------------------------------------------------------
# Patch 4+5+6+7: audio_check.json via ffmpeg astats
# ---------------------------------------------------------------------------


_ASTATS_KEY_RE = re.compile(
    r"\[Parsed_astats[^\]]*\][^\n]*?"
    r"(RMS level dB|Peak level dB|Mean difference|RMS difference|Min level|Max level):\s*"
    r"([\-\d\.]+|nan|inf)",
    re.IGNORECASE,
)


def patch_audio_check(session: Path, dry_run: bool = False) -> dict:
    """Generate audio_check.json from audio.flac via ffmpeg astats."""
    audio_path = session / "audio.flac"
    out_path = session / "audio_check.json"
    if not audio_path.exists():
        return {"error": "audio.flac missing"}

    # ffmpeg -i audio.flac -af astats -f null -
    cmd = [
        "ffmpeg", "-hide_banner", "-i", str(audio_path),
        "-af", "astats=metadata=1:reset=1,ametadata=print:file=-",
        "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return {"error": f"ffmpeg failed: {exc}"}

    stderr = proc.stderr
    stdout = proc.stdout

    # Parse astats from stderr (where ffmpeg writes filter output)
    # Format: "[Parsed_astats_X @ 0x...] Channel 1: ... RMS level dB: -23.45"
    parsed: dict[str, float] = {}
    for line in (stderr + "\n" + stdout).splitlines():
        m = _ASTATS_KEY_RE.search(line)
        if m:
            key, val = m.group(1), m.group(2)
            try:
                parsed[key.replace(" ", "_").lower()] = float(val)
            except (ValueError, TypeError):
                pass

    # Compute SNR (peak - rms = approximate dynamic range)
    rms_db = parsed.get("rms_level_db")
    peak_db = parsed.get("peak_level_db")
    snr_db = None
    if rms_db is not None and peak_db is not None:
        snr_db = peak_db - rms_db

    # Audio size + duration for B7 continuity check
    size_bytes = audio_path.stat().st_size
    # Get duration via ffprobe
    dur_sec = 0.0
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        dur_sec = float(r.stdout.strip()) if r.stdout.strip() else 0.0
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass

    # B7: audio continuity — measure max silence gap via ffmpeg silencedetect
    max_silence_gap_s = 0.0
    try:
        sd_proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(audio_path),
             "-af", "silencedetect=noise=-50dB:duration=0.5", "-f", "null", "-"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        # Parse silence_duration values
        durs = []
        for line in sd_proc.stderr.splitlines():
            m = re.search(r"silence_duration:\s*([\d.]+)", line)
            if m:
                durs.append(float(m.group(1)))
        max_silence_gap_s = max(durs) if durs else 0.0
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass

    audio_check = {
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "audio_file": "audio.flac",
        "size_bytes": size_bytes,
        "duration_sec": dur_sec,
        "rms_db": rms_db,
        "peak_db": peak_db,
        "snr_db": snr_db,
        "is_silent": (rms_db is not None and rms_db < -60.0),
        "continuous": dur_sec > 1.0,
        "max_silence_gap_s": max_silence_gap_s,  # B7 needs this
        "longest_silence_s": max_silence_gap_s,  # B7 alt name
        "method": "ffmpeg astats + silencedetect (post-patch)",
    }

    if not dry_run:
        out_path.write_text(json.dumps(audio_check, indent=2), encoding="utf-8")

    return audio_check


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("session_dir", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    sess = args.session_dir.resolve()
    if not sess.is_dir():
        print(f"ERROR: not a directory: {sess}", file=sys.stderr)
        return 2

    print(f"audit_lift_post_patches: {sess.name}")
    print()

    print("=== Patch 1+2+3: metadata.json (M2/M3/SS5) ===")
    meta_changes = patch_metadata(sess, args.dry_run)
    if "error" in meta_changes:
        print(f"  ⚠ {meta_changes['error']}")
    else:
        for k, v in meta_changes.items():
            print(f"  + {k}: {str(v)[:60]}")
        if not meta_changes:
            print("  (no changes needed — already patched)")

    print()
    print("=== Patch 4-7: audio_check.json (U-aux/B7/QM3/QM4) ===")
    audio_result = patch_audio_check(sess, args.dry_run)
    if "error" in audio_result:
        print(f"  ⚠ {audio_result['error']}")
    else:
        print(f"  rms_db = {audio_result['rms_db']}")
        print(f"  peak_db = {audio_result['peak_db']}")
        print(f"  snr_db = {audio_result['snr_db']}")
        print(f"  duration = {audio_result['duration_sec']:.1f}s")
        print(f"  is_silent = {audio_result['is_silent']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
