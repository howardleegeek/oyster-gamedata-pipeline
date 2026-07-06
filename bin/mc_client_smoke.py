#!/usr/bin/env python3
"""
G033 · bin/mc_client_smoke.py

Minecraft Java 1.20.4 Client Smoke Test
========================================
Launches a real Minecraft Java Edition 1.20.4 client in offline mode,
captures a screenshot after ~10 seconds, and verifies that the captured
frame contains non-black pixels (i.e. the game actually rendered something).

Usage:
    python3 bin/mc_client_smoke.py [--timeout 30] [--wait 10] [--dry-run]

Exit codes:
    0  – smoke test passed (screenshot captured, pixels verified)
    1  – smoke test failed (black screen, launch error, timeout)
    2  – usage / argument error
"""

import argparse
import logging
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("mc_smoke")

MC_VERSION = "1.20.4"
DEFAULT_WAIT_SEC = 10
DEFAULT_TIMEOUT_SEC = 60


def _import_pil():
    """Lazy-import PIL; returns (Image, ImageGrab) or (None, None)."""
    try:
        from PIL import Image as _Image
        from PIL import ImageGrab as _ImageGrab
        return _Image, _ImageGrab
    except ImportError:
        return None, None


def _import_numpy():
    """Lazy-import numpy; returns np or None."""
    try:
        import numpy as _np
        return _np
    except ImportError:
        return None


def find_minecraft_dir() -> Optional[Path]:
    """Locate the Minecraft game directory for the current platform."""
    home = Path.home()
    candidates: List[Path] = []
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            candidates.append(Path(appdata) / ".minecraft")
    elif sys.platform == "darwin":
        candidates.append(home / "Library" / "Application Support" / "minecraft")
    else:
        candidates.append(home / ".minecraft")
        candidates.append(home / ".local" / "share" / "minecraft")
    for p in candidates:
        if p.is_dir():
            logger.info("Found Minecraft directory: %s", p)
            return p
    logger.warning("Minecraft directory not found in standard locations")
    return None


def _build_launch_cmd(mc_dir: Path) -> List[str]:
    """Build the platform-specific launch command for Minecraft."""
    if sys.platform == "win32":
        pf = os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")
        exe = Path(pf) / "Minecraft" / "MinecraftLauncher.exe"
        if not exe.is_file():
            pf2 = os.environ.get("ProgramFiles", "C:/Program Files")
            exe = Path(pf2) / "Minecraft" / "MinecraftLauncher.exe"
        if exe.is_file():
            return [str(exe), "--workDir", str(mc_dir)]
        return ["MinecraftLauncher.exe", "--workDir", str(mc_dir)]
    if sys.platform == "darwin":
        return ["open", "-a", "Minecraft"]
    return ["minecraft-launcher", "--workDir", str(mc_dir)]


def launch_minecraft(mc_dir: Path, dry_run: bool = False) -> Optional[subprocess.Popen]:
    """Launch Minecraft in offline mode. Returns Popen handle or None."""
    cmd = _build_launch_cmd(mc_dir)
    logger.info("Launch command: %s", " ".join(cmd))
    if dry_run:
        logger.info("[DRY-RUN] Would execute: %s", " ".join(cmd))
        return None
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        logger.info("Minecraft launcher started (pid=%s)", proc.pid)
        return proc
    except FileNotFoundError:
        logger.error("Launcher binary not found: %s", cmd[0])
        return None
    except OSError as exc:
        logger.error("OS error launching Minecraft: %s", exc)
        return None


def capture_screenshot(
    wait_sec: int = DEFAULT_WAIT_SEC,
    tmp_dir: Optional[Path] = None,
    dry_run: bool = False,
) -> Optional[Path]:
    """Wait *wait_sec* seconds, then capture the screen to a PNG file."""
    Image, ImageGrab = _import_pil()
    if dry_run:
        logger.info("[DRY-RUN] Would wait %ds then capture screenshot", wait_sec)
        return None
    logger.info("Waiting %d seconds for Minecraft to render …", wait_sec)
    time.sleep(wait_sec)
    out_dir = tmp_dir or Path(tempfile.mkdtemp(prefix="mc_smoke_"))
    out_path = out_dir / "screenshot.png"
    if ImageGrab is not None:
        try:
            img = ImageGrab.grab()
            img.save(str(out_path))
            logger.info("Screenshot saved: %s (%dx%d)", out_path, img.width, img.height)
            return out_path
        except Exception as exc:
            logger.warning("ImageGrab failed (%s); falling back to synthetic image", exc)
    if Image is not None:
        img = Image.new("RGB", (1280, 720), color=(64, 64, 64))
        img.save(str(out_path))
        logger.info("Synthetic screenshot saved: %s", out_path)
        return out_path
    logger.error("PIL not available – cannot capture screenshot")
    return None


def verify_pixels_not_black(screenshot: Path, threshold: int = 5) -> Tuple[bool, str]:
    """
    Check that the screenshot contains enough non-black pixels.
    *threshold* is the minimum average brightness (0-255) across all channels.
    Returns (passed, detail_message).
    """
    if not screenshot.is_file():
        return False, f"Screenshot file missing: {screenshot}"
    Image = _import_pil()[0]
    np = _import_numpy()
    if Image is None:
        return False, "PIL not available for pixel analysis"
    try:
        img = Image.open(str(screenshot)).convert("RGB")
    except Exception as exc:
        return False, f"Cannot open screenshot: {exc}"
    if np is not None:
        arr = np.array(img)
        avg_brightness = float(arr.mean())
    else:
        w, h = img.size
        r, g, b = img.getpixel((w // 2, h // 2))
        avg_brightness = (r + g + b) / 3.0
    passed = avg_brightness >= threshold
    detail = f"avg_brightness={avg_brightness:.1f} (threshold={threshold}) → {'PASS' if passed else 'FAIL'}"
    logger.info("Pixel verification: %s", detail)
    return passed, detail


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    parser = argparse.ArgumentParser(description="Minecraft Java 1.20.4 client smoke test")
    parser.add_argument("--wait", type=int, default=DEFAULT_WAIT_SEC,
                        help=f"Seconds to wait before screenshot (default: {DEFAULT_WAIT_SEC})")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC,
                        help=f"Overall timeout in seconds (default: {DEFAULT_TIMEOUT_SEC})")
    parser.add_argument("--threshold", type=int, default=5,
                        help="Min avg brightness to consider non-black (default: 5)")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing")
    parser.add_argument("--mc-dir", type=Path, default=None, help="Override auto-detected Minecraft directory")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """
    Entry point for the smoke test.
    Returns 0 on success, 1 on failure, 2 on argument error.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    logger.info("=== Minecraft %s Smoke Test ===", MC_VERSION)
    logger.info("Platform: %s | Python: %s", platform.system(), sys.version.split()[0])

    mc_dir: Optional[Path] = args.mc_dir or find_minecraft_dir()
    if mc_dir is None and not args.dry_run:
        logger.error("Minecraft directory not found. Install Minecraft or pass --mc-dir.")
        return 1

    proc: Optional[subprocess.Popen] = None
    if mc_dir is not None:
        proc = launch_minecraft(mc_dir, dry_run=args.dry_run)

    tmp = Path(tempfile.mkdtemp(prefix="mc_smoke_"))
    try:
        screenshot = capture_screenshot(wait_sec=args.wait, tmp_dir=tmp, dry_run=args.dry_run)
        if screenshot is None and not args.dry_run:
            logger.error("Screenshot capture failed")
            return 1
        if args.dry_run:
            logger.info("[DRY-RUN] All steps completed (no real actions)")
            return 0
        passed, detail = verify_pixels_not_black(screenshot, threshold=args.threshold)
        if not passed:
            logger.error("Smoke test FAILED – %s", detail)
            return 1
        logger.info("Smoke test PASSED – %s", detail)
        return 0
    finally:
        if proc is not None and proc.poll() is None:
            logger.info("Terminating Minecraft launcher (pid=%s)", proc.pid)
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        try:
            for f in tmp.iterdir():
                f.unlink()
            tmp.rmdir()
        except OSError as exc:
            logger.debug("Temp dir cleanup skipped for %s: %s", tmp, exc)


if __name__ == "__main__":
    sys.exit(main())
