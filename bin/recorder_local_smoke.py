#!/usr/bin/env python3
"""recorder_local_smoke.py — End-to-end local smoke test for the recorder pipeline.

Orchestrates the full recording flow **without** real OBS, real Minecraft,
or a real cloud backend:

  1. Call ``mock_game_detector`` → get fake game detection
  2. Call ``mock_obs_recorder`` → write fake mp4 + real metadata.json
  3. Upload to backend (real HTTP to ``--backend-url``)
  4. Verify the session was received by the backend

Usage
-----
    # Start the backend stub first:
    python3 bin/backend_stub.py --port 8500 &

    # Then run the smoke test:
    python3 bin/recorder_local_smoke.py --backend-url http://localhost:8500

    # Or use the configured backend URL from ~/.oyster/config.json:
    python3 bin/recorder_local_smoke.py

Output
------
    Prints ``BUYER_READY`` on success, ``FAIL: <step>`` on any failure.

Exit codes
----------
    0 — all steps passed (BUYER_READY printed)
    1 — one or more steps failed (FAIL: <step> printed)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------


def step_detect_game() -> Dict[str, Any]:
    """Step 1: Run mock game detector and return detection result."""
    from bin.mock_game_detector import detect_game

    result = detect_game()
    logger.info("Game detected: %s", json.dumps(result))
    return result


def step_record(
    output_dir: Path,
    detection: Dict[str, Any],
    session_id: str,
) -> Dict[str, Path]:
    """Step 2: Run mock OBS recorder to write fake mp4 + metadata.json."""
    from bin.mock_obs_recorder import write_fake_recording

    result = write_fake_recording(
        output_dir,
        session_id=session_id,
        game=detection.get("game", "minecraft"),
        pid=detection.get("pid", 12345),
        window_title=detection.get("window_title", "MC 1.21.4"),
    )
    logger.info("Recording written: %s", json.dumps({k: str(v) for k, v in result.items()}))
    return result


def step_upload(
    backend_url: str,
    session_id: str,
    video_path: Path,
    metadata_path: Path,
) -> Dict[str, Any]:
    """Step 3: Upload the recording to the backend stub."""
    import httpx

    metadata = json.loads(metadata_path.read_text())

    with open(video_path, "rb") as vf:
        files = {"video": (video_path.name, vf, "video/mp4")}
        data = {
            "session_id": session_id,
            "metadata_json": json.dumps(metadata),
            "game": metadata.get("game", "minecraft"),
            "pid": str(metadata.get("pid", 12345)),
            "window_title": metadata.get("window_title", "MC 1.21.4"),
            "device_id": metadata.get("device_id", ""),
        }

        upload_url = f"{backend_url.rstrip('/')}/v1/sessions"
        logger.info("Uploading to %s", upload_url)

        with httpx.Client(timeout=10.0) as client:
            resp = client.post(upload_url, data=data, files=files)
            resp.raise_for_status()
            return resp.json()


def step_verify(
    backend_url: str,
    session_id: str,
) -> Dict[str, Any]:
    """Step 4: Verify the session exists on the backend."""
    import httpx

    verify_url = f"{backend_url.rstrip('/')}/v1/sessions/{session_id}"
    logger.info("Verifying session at %s", verify_url)

    with httpx.Client(timeout=10.0) as client:
        resp = client.get(verify_url)
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_smoke(backend_url: str) -> int:
    """Run the full smoke pipeline. Returns 0 on success, 1 on failure."""
    import uuid

    session_id = str(uuid.uuid4())
    logger.info("Session ID: %s", session_id)

    # Step 1: Detect game
    try:
        logger.info("=== Step 1: Game Detection ===")
        detection = step_detect_game()
        assert detection.get("game") == "minecraft", "Unexpected game name"
        assert detection.get("pid") == 12345, "Unexpected pid"
        logger.info("Step 1 PASSED")
    except Exception as exc:
        logger.error("FAIL: detect_game — %s", exc)
        print(f"FAIL: detect_game — {exc}")
        return 1

    # Step 2: Record (fake)
    try:
        logger.info("=== Step 2: Recording ===")
        with tempfile.TemporaryDirectory(prefix="smoke_clip_") as tmpdir:
            output_dir = Path(tmpdir)
            files = step_record(output_dir, detection, session_id)

            assert files["video"].exists(), "Video file not written"
            assert files["metadata"].exists(), "Metadata file not written"
            assert files["video"].stat().st_size > 0, "Video file is empty"

            # Validate metadata.json content
            meta = json.loads(files["metadata"].read_text())
            assert meta["session_id"] == session_id, "Session ID mismatch in metadata"
            assert meta["game"] == "minecraft", "Game mismatch in metadata"

            logger.info("Step 2 PASSED")

            # Step 3: Upload
            try:
                logger.info("=== Step 3: Upload ===")
                upload_result = step_upload(
                    backend_url, session_id, files["video"], files["metadata"]
                )
                uploaded_session_id = upload_result.get("session_id")
                assert uploaded_session_id, "Upload response missing session_id"
                session_id = uploaded_session_id
                assert upload_result.get("status") == "received", "Upload status not 'received'"
                logger.info("Step 3 PASSED")
            except Exception as exc:
                logger.error("FAIL: upload — %s", exc)
                print(f"FAIL: upload — {exc}")
                return 1

            # Step 4: Verify
            try:
                logger.info("=== Step 4: Verify ===")
                verify_result = step_verify(backend_url, session_id)
                assert (
                    verify_result.get("session_id") == session_id
                ), "Verify returned wrong session_id"
                assert verify_result.get("status") == "received", "Verify status not 'received'"
                logger.info("Step 4 PASSED")
            except Exception as exc:
                logger.error("FAIL: verify — %s", exc)
                print(f"FAIL: verify — {exc}")
                return 1

    except Exception as exc:
        logger.error("FAIL: record — %s", exc)
        print(f"FAIL: record — {exc}")
        return 1

    print("BUYER_READY")
    return 0


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    from bin.recorder_config import load as load_config

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--backend-url",
        default=None,
        help="Backend stub URL (e.g. http://localhost:8500). "
        "Defaults to value from ~/.oyster/config.json.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging",
    )
    args = parser.parse_args(argv)

    # Resolve backend URL: CLI arg > env var (via recorder_config) > config file
    if args.backend_url is None:
        cfg = load_config()
        args.backend_url = cfg["backend_url"]

    return args


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return run_smoke(args.backend_url)


if __name__ == "__main__":
    sys.exit(main())
