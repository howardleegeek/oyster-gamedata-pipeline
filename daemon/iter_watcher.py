#!/usr/bin/env python3
"""iter_watcher — hourly cron daemon that watches PRD compliance audit results.

Every hour (or once with --once):
1. Runs bin/prd_compliance_audit.py against the latest finalized session
   (falls back to a synthetic fixture if no session exists).
2. Finds all FAIL / SKIP items.
3. For each FAIL, auto-generates a spec draft in specs/auto/.
4. Deduplicates by gate_id + day (never overwrites existing auto-specs).

Usage:
    python3 daemon/iter_watcher.py --once          # single run, writes specs
    python3 daemon/iter_watcher.py --once --dry-run  # single run, print only
    python3 daemon/iter_watcher.py                  # daemon loop (sleep 3600s)
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("iter_watcher")

# ---------------------------------------------------------------------------
# Paths (relative to repo root)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
AUDIT_SCRIPT = REPO_ROOT / "bin" / "prd_compliance_audit.py"
AUTO_SPECS_DIR = REPO_ROOT / "specs" / "auto"

# ---------------------------------------------------------------------------
# Synthetic fixture — used when no finalized session is found
# ---------------------------------------------------------------------------
SYNTHETIC_SESSION_DIR = REPO_ROOT / "tests" / "fixtures" / "synthetic_session"


def _build_synthetic_session() -> Path:
    """Create a minimal synthetic session directory for audit testing.

    Returns the session directory path.
    """
    session = SYNTHETIC_SESSION_DIR
    session.mkdir(parents=True, exist_ok=True)

    # recording.mp4 — 1 s black frame via ffmpeg (or a tiny placeholder)
    mp4 = session / "recording.mp4"
    if not mp4.exists():
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=640x480:d=1",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-y",
                    str(mp4),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
        except (
            FileNotFoundError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
        ):
            # Fallback: write a minimal valid MP4 header (ftyp box only)
            mp4.write_bytes(b"\x00\x00\x00\x1cftypisom\x00\x00\x02\x00isomiso2mp41")

    # action_camera.json — intentionally missing PRD fields to trigger FAILs
    action_camera = [
        {
            "timestamp": "2024-01-01T00:00:00.000Z",
            "action": "START_RECORDING",
            "camera_id": "cam_001",
        }
    ]
    (session / "action_camera.json").write_text(json.dumps(action_camera, indent=2))

    # game_state.jsonl — 20 ticks
    lines = []
    for i in range(20):
        lines.append(
            json.dumps(
                {
                    "tick": i,
                    "timestamp": f"2024-01-01T00:00:{i:02d}.000Z",
                    "player_position": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "player_health": 100,
                    "game_phase": "PLAYING",
                }
            )
        )
    (session / "game_state.jsonl").write_text("\n".join(lines) + "\n")

    # frames.jsonl
    (session / "frames.jsonl").write_text(
        json.dumps(
            {
                "frame_id": 0,
                "timestamp": "2024-01-01T00:00:00.000Z",
                "width": 640,
                "height": 480,
                "format": "RGB24",
            }
        )
        + "\n"
    )

    # inputs.jsonl
    (session / "inputs.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2024-01-01T00:00:00.500Z",
                "device": "KEYBOARD",
                "action": "PRESS",
                "key": "SPACE",
                "frame_id": 0,
            }
        )
        + "\n"
    )

    # metadata.json — minimal
    (session / "metadata.json").write_text(
        json.dumps(
            {
                "session_id": "synth-001",
                "recording_date": "2024-01-01",
                "game_name": "synthetic_game",
                "operator_id": "OP-000",
            },
            indent=2,
        )
    )

    # gameinfo.xlsx — we write a CSV fallback since openpyxl may not be installed
    gameinfo_csv = session / "gameinfo.csv"
    if not (session / "gameinfo.xlsx").exists():
        gameinfo_csv.write_text(
            "game_name,game_version,platform,scene_name,weather,"
            "time_of_day,character_name,character_class,operator_id,"
            "recording_date,total_frames,video_duration_sec,route_type,notes\n"
            "synthetic_game,1.0,linux,test_scene,clear,day,hero,warrior,"
            "OP-000,2024-01-01,20,1.0,linear,synthetic fixture\n"
        )

    # systeminfo.json
    (session / "systeminfo.json").write_text(
        json.dumps(
            {
                "os": "linux",
                "cpu": "x86_64",
                "gpu": "none",
                "ram_gb": 8,
            },
            indent=2,
        )
    )

    # audio_check.json
    (session / "audio_check.json").write_text(
        json.dumps(
            {
                "has_audio": False,
                "sample_rate": 0,
                "channels": 0,
            },
            indent=2,
        )
    )

    # MANIFEST.json
    (session / "MANIFEST.json").write_text(
        json.dumps(
            {
                "version": 1,
                "files": ["recording.mp4", "action_camera.json", "game_state.jsonl"],
            },
            indent=2,
        )
    )

    # depth/.source
    (session / "depth").mkdir(exist_ok=True)
    (session / "depth" / ".source").write_text(
        json.dumps({"kind": "ci_fixture", "frame_count": 20, "gap_miss_ratio": 0.0})
    )

    return session


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------


def find_latest_session() -> Path | None:
    """Find the most recently modified finalized session directory.

    Looks under common session storage locations. Returns None if no
    finalized session is found.
    """
    candidates: list[Path] = []
    for search_root in [
        REPO_ROOT / "sessions",
        REPO_ROOT / "data" / "sessions",
        REPO_ROOT / "output" / "sessions",
        REPO_ROOT / "recordings",
    ]:
        if search_root.is_dir():
            candidates.extend(search_root.iterdir())

    # Filter to directories that look like finalized sessions
    finalized = [d for d in candidates if d.is_dir() and (d / "recording.mp4").exists()]
    if not finalized:
        return None

    return max(finalized, key=lambda d: d.stat().st_mtime)


def get_session_dir() -> Path:
    """Return the session dir to audit — real or synthetic."""
    real = find_latest_session()
    if real is not None:
        log.info("Using latest finalized session: %s", real)
        return real

    log.info("No finalized session found — building synthetic fixture")
    return _build_synthetic_session()


# ---------------------------------------------------------------------------
# Audit runner
# ---------------------------------------------------------------------------


def run_audit(session_dir: Path) -> list[dict]:
    """Run prd_compliance_audit.py --json against *session_dir*.

    Returns the list of audit items (each a dict with id/status/evidence).
    """
    cmd = [
        sys.executable,
        str(AUDIT_SCRIPT),
        str(session_dir),
        "--json",
    ]
    log.info("Running audit: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(REPO_ROOT),
    )

    if result.returncode not in (0, 1):
        # 0 = all pass, 1 = some fail, anything else = error
        log.error(
            "Audit exited with code %d: %s", result.returncode, result.stderr[:500]
        )
        raise RuntimeError(f"Audit failed with exit code {result.returncode}")

    # Parse JSON from stdout
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        log.error("Failed to parse audit JSON: %s", exc)
        log.debug("Audit stdout (first 1000 chars): %s", result.stdout[:1000])
        raise

    return report.get("items", [])


# ---------------------------------------------------------------------------
# Spec generation
# ---------------------------------------------------------------------------


def _spec_filename(gate_id: str, ts: datetime) -> str:
    """Return the auto-spec filename for a given gate and timestamp."""
    date_part = ts.strftime("%Y%m%d-%H%M")
    return f"auto-{date_part}-{gate_id}.md"


def _existing_spec_for_gate(gate_id: str, day: str) -> Path | None:
    """Check if a spec already exists for this gate_id on this day.

    Returns the existing file path, or None.
    """
    if not AUTO_SPECS_DIR.is_dir():
        return None
    prefix = f"auto-{day}-"
    for f in AUTO_SPECS_DIR.iterdir():
        if f.name.startswith(prefix) and f.name.endswith(f"-{gate_id}.md"):
            return f
    return None


def _generate_spec_content(gate_id: str, evidence: str, ts: datetime) -> str:
    """Generate the full markdown content for an auto-spec draft."""
    task_id = f"auto-{ts.strftime('%Y%m%d-%H%M')}-{gate_id}"
    date_str = ts.strftime("%Y-%m-%d %H:%M")

    return f"""\
---
task_id: {task_id}
project: gamedata-pipeline
priority: 2
estimated_minutes: 30
depends_on: []
modifies: []
executor: iter-watcher-auto
source_audit_gate: {gate_id}
generated_at: "{date_str}"
---

## 问题描述

PRD compliance audit gate **{gate_id}** 报告 FAIL。

**审计证据：**

{evidence}

## 根因分析

> TODO: 分析 {gate_id} 失败的根本原因。
> 检查 session 数据、录制管线、以及 PRD 合规要求。

## 修复方案

> TODO: 提出修复 {gate_id} 的具体方案。
> 可能涉及：数据修复、管线调整、PRD 更新。

## 验收标准

- [ ] `bin/prd_compliance_audit.py` 对 {gate_id} 返回 PASS
- [ ] 修复不引入新的 FAIL 项
- [ ] 相关文档已更新
"""


def generate_specs(
    items: list[dict],
    ts: datetime,
    *,
    dry_run: bool = False,
) -> list[Path]:
    """Generate spec drafts for all FAIL/SKIP items.

    Returns list of written (or would-be-written) file paths.
    """
    AUTO_SPECS_DIR.mkdir(parents=True, exist_ok=True)
    day = ts.strftime("%Y%m%d")
    written: list[Path] = []

    for item in items:
        status = item.get("status", "")
        if status not in ("FAIL", "SKIP"):
            continue

        gate_id = item.get("id", "unknown")
        evidence = item.get("evidence", "No evidence provided.")

        # Dedup: skip if spec already exists for this gate_id today
        existing = _existing_spec_for_gate(gate_id, day)
        if existing is not None:
            log.info("Skipping %s — spec already exists: %s", gate_id, existing)
            continue

        filename = _spec_filename(gate_id, ts)
        filepath = AUTO_SPECS_DIR / filename
        content = _generate_spec_content(gate_id, evidence, ts)

        if dry_run:
            log.info("[DRY-RUN] Would write spec: %s", filepath)
            log.info("[DRY-RUN] --- spec preview ---")
            for line in content.splitlines()[:15]:
                log.info("[DRY-RUN]   %s", line)
            log.info("[DRY-RUN] ...")
            written.append(filepath)
        else:
            filepath.write_text(content, encoding="utf-8")
            log.info("Wrote spec: %s", filepath)
            written.append(filepath)

    return written


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run_once(*, dry_run: bool = False) -> list[Path]:
    """Execute one full cycle: audit → find fails → generate specs."""
    ts = datetime.now(timezone.utc)
    log.info("=== iter_watcher cycle start (dry_run=%s) ===", dry_run)

    session_dir = get_session_dir()
    log.info("Session dir: %s", session_dir)

    items = run_audit(session_dir)
    log.info("Audit complete: %d items total", len(items))

    fail_skip = [it for it in items if it.get("status") in ("FAIL", "SKIP")]
    log.info("FAIL/SKIP items: %d", len(fail_skip))

    if not fail_skip:
        log.info("All items PASS — no specs to generate")
        return []

    written = generate_specs(fail_skip, ts, dry_run=dry_run)
    log.info("Generated %d spec(s)", len(written))
    return written


def run_daemon(*, dry_run: bool = False, interval: int = 3600) -> None:
    """Run the watcher in a loop, sleeping *interval* seconds between cycles."""
    log.info(
        "Starting iter_watcher daemon (interval=%ds, dry_run=%s)", interval, dry_run
    )
    while True:
        try:
            run_once(dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            log.debug("run_once() raised: %s", exc)
            log.exception("Cycle failed — will retry after sleep")
        log.info("Sleeping %d seconds …", interval)
        time.sleep(interval)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Iter watcher daemon — auto-generate specs from audit failures",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single cycle and exit (default: daemon loop)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without writing files",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=3600,
        help="Sleep interval in seconds for daemon mode (default: 3600)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.once:
        run_once(dry_run=args.dry_run)
    else:
        run_daemon(dry_run=args.dry_run, interval=args.interval)

    return 0


if __name__ == "__main__":
    sys.exit(main())
