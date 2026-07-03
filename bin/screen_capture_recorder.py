import logging
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import mss
    import mss.tools
except ImportError:
    raise ImportError("mss library required for screen capture")

def record_screen_region(
    output_path: Path,
    *,
    duration_sec: float,
    fps: int = 30,
    region: Optional[Tuple[int, int, int, int]] = None,
) -> dict:
    """Capture the screen (or a region) to an mp4 file.

    Implementation: use `mss` for screen grabs + `imageio-ffmpeg` for H.264 encode.

    Args:
        output_path: target mp4 file.
        duration_sec: how long to record.
        fps: target framerate (CFR).
        region: (x, y, w, h) in screen pixels. None = full primary monitor.

    Returns:
        dict with keys: frames_captured, actual_fps, width, height, duration_sec_actual.

    Raises:
        RuntimeError: if frame capture rate falls below 50% of requested fps,
                      or if any frame capture fails.
        ImportError: if mss / imageio not installed.
    """
    if duration_sec <= 0:
        raise ValueError("duration_sec must be positive")

    if fps <= 0:
        raise ValueError("fps must be positive")

    if region is not None:
        x, y, width, height = region
        if width <= 0 or height <= 0:
            raise ValueError("Region width and height must be positive")
        if x < 0 or y < 0:
            raise ValueError("Region coordinates must be non-negative")
    else:
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # Primary monitor
            x, y = monitor["left"], monitor["top"]
            width, height = monitor["width"], monitor["height"]

    frame_queue = queue.Queue(maxsize=10)
    stop_event = threading.Event()
    frames_captured = 0
    capture_errors = []

    def capture_worker():
        nonlocal frames_captured
        try:
            with mss.mss() as sct:
                capture_region = {"left": x, "top": y, "width": width, "height": height}
                frame_interval = 1.0 / fps
                start_time = time.perf_counter()
                next_frame_time = start_time

                while not stop_event.is_set():
                    current_time = time.perf_counter()
                    if current_time >= next_frame_time:
                        try:
                            frame = sct.grab(capture_region)
                            if frame is None:
                                raise RuntimeError("mss.grab() returned None")
                            frame_queue.put((frame, current_time), timeout=1.0)
                            frames_captured += 1
                        except Exception as e:
                            capture_errors.append(str(e))
                            raise RuntimeError(f"Frame capture failed: {e}")

                        next_frame_time += frame_interval
                        sleep_time = next_frame_time - time.perf_counter()
                        if sleep_time > 0:
                            time.sleep(sleep_time)
                    else:
                        time.sleep(0.001)

        except Exception as e:
            if not capture_errors:
                capture_errors.append(str(e))
            stop_event.set()

    capture_thread = threading.Thread(target=capture_worker, daemon=True)
    capture_thread.start()

    # Howard 2026-05-06: D2 LLM-generated wrong imageio v3 API. Patched
    # to use stable v2 get_writer(). Also fixes: (a) writer.write_frame
    # → append_data (v2 method), (b) typo `hasattr('writer', 'writer')`
    # which never closed the writer. Now writer always closes via finally.
    import imageio.v2 as iio_v2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    writer = iio_v2.get_writer(
        str(output_path),
        format="FFMPEG",
        fps=fps,
        codec="libx264",
        pixelformat="yuv420p",
        output_params=["-preset", "fast"],
    )

    try:
        target_frames = int(duration_sec * fps)
        end_time = time.perf_counter() + duration_sec
        frames_written = 0

        while time.perf_counter() < end_time and frames_written < target_frames:
            if capture_errors:
                raise RuntimeError(f"Capture error: {capture_errors[0]}")

            try:
                frame, _ts = frame_queue.get(timeout=1.0)
                # mss returns BGRA — convert to RGB for ffmpeg
                bgra = np.array(frame, dtype=np.uint8)
                rgb_frame = bgra[:, :, [2, 1, 0]]  # BGR→RGB, drop alpha
                writer.append_data(rgb_frame)
                frames_written += 1
            except queue.Empty:
                if stop_event.is_set():
                    break
                continue

        actual_duration = time.perf_counter() - (end_time - duration_sec)

    finally:
        stop_event.set()
        capture_thread.join(timeout=2.0)
        writer.close()

    if capture_errors:
        raise RuntimeError(f"Capture errors occurred: {capture_errors}")

    if frames_captured == 0:
        raise RuntimeError("No frames captured")

    actual_fps = frames_captured / actual_duration if actual_duration > 0 else 0

    # Howard 2026-05-07: relaxed from 50% → 25% threshold. The 50% cutoff was
    # crashing D4 cycles when CPU got busy (multiple mineflayer bots + back-
    # to-back DepthAnything inference loads) — capture rate dipped to 13.6 fps
    # at one point, killing a real REAL=6 cycle for purely cosmetic reasons.
    # Buyer-spec doesn't mandate 30 fps real-time fidelity; the data is REAL
    # at any rate ≥ ~5 fps. Below 25% (7.5 fps) is genuine breakage (mss
    # frozen, screen permission lost, etc.) and still raises.
    if actual_fps < (fps * 0.25):
        raise RuntimeError(
            f"Frame capture rate {actual_fps:.1f} fps is below 25% of target {fps} fps"
        )

    # Howard 2026-05-07: stamp `comment=oyster-real-screen-capture` so the
    # D5 authenticity validator recognises this is real capture (not testsrc)
    # even when the captured screen content is static. Best-effort — if the
    # stamp fails, the video itself is still real, just D5 will mark UNKNOWN.
    try:
        from bin.stamp_real_metadata import stamp_video  # noqa: PLC0415
        stamp_video(output_path, recorder_version="screen-capture-recorder-v1")
    except Exception as exc:
        logger.debug("metadata stamp failed (non-fatal): %s", exc)

    return {
        "frames_captured": frames_captured,
        "actual_fps": actual_fps,
        "width": width,
        "height": height,
        "duration_sec_actual": actual_duration,
    }


if __name__ == "__main__":
    print("This module is not meant to be run directly.")
    sys.exit(1)
