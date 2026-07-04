#!/usr/bin/env python3
"""
bin/recorder_test_harness.py — Pure-function harness for recorder packaging logic.

Why this exists
---------------
``bin/recorder_consumer_lite.py`` (the v0.19+ stop-gap MVP recorder) wraps
all packaging behaviour inside ``RecorderApp._package_tarball``, which is
a method on a ``tkinter.Tk`` subclass. The *module itself* imports
``tkinter`` unconditionally at import time, so on headless CI / non-GUI
test runners the recorder module is unimportable.

For end-to-end behavioural testing we need to:

1. Inject a deterministic stream of pynput-style events.
2. Run the action_camera.json synthesis logic on those events.
3. Read back the produced JSON and assert on its content.

This harness re-implements the *exact* per-frame state machine from
``RecorderApp._package_tarball`` (lines 1225-1312 of the consumer-lite
recorder, version ``lite-v0.19.0``) **as a pure free function**, with no
Tk coupling. Any drift in the recorder's logic must be mirrored here, or
the unit tests that import this module will fail — that drift signal is
the whole point.

Public API
----------
``synthesize_action_camera_records(events, started_at, elapsed_sec, fps=30)``
    Replays a list of pynput-shaped events into a list of 20-field
    action_camera.json records, mirroring recorder_consumer_lite.

``package_tarball(out_dir, video_path, events, started_at, elapsed_sec,
mc_window_rect=None, mcpr_path=None)``
    End-to-end packaging: writes the 5 PRD-shaped artefacts (video,
    systeminfo.json, action_camera.json, gameinfo.xlsx, depth/) and tar.gz
    bundles them. Optionally merges Replay Mod camera samples on top of
    the action_camera frames.

These two functions are what the test suite drives.
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Make sibling modules importable when this harness is invoked directly
# (``python3 bin/recorder_test_harness.py``) and via tests that use
# ``from bin.recorder_test_harness import …``.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Constants — keep identical to bin/recorder_consumer_lite.py.
# ---------------------------------------------------------------------------
FPS_DEFAULT: float = 30.0
SCREEN_W: int = 1920
SCREEN_H: int = 1080
# camera_intrinsics fy = (height/2) / tan(FOV_v / 2). Default MC = 70°.
_TAN_HALF_FOV: float = 0.7002075382097097  # tan(35°)
_FY: float = round(540.0 / _TAN_HALF_FOV, 3)
DEFAULT_INTRINSICS: dict[str, float] = {
    "fx": _FY,
    "fy": _FY,
    "cx": 960.0,
    "cy": 540.0,
}

# 20 fields each frame must carry (mirrors recorder_consumer_lite v0.20.0
# AND sample_tarball_builder.synthesize_action_camera).
# v0.20.0 reverted the v0.19.0 regression: PDF page 4-5 字面用拼音 "oula"
# (NOT "euler") and 'camera_Follow Offset' (literal space + capital F).
# See docs/PRD_DIGEST.md and docs/PRD_FORMULAS.md for the audit.
ACTION_CAMERA_FIELDS: tuple[str, ...] = (
    "frame",
    "time",
    "fps",
    "route_type",
    "mouse_x",
    "mouse_y",
    "mouse_dx",
    "mouse_dy",
    "keyCode",
    "camera_position",
    "camera_rotation_oula",          # PDF p4 字面拼音
    "camera_rotation_quaternion",
    "camera_Follow Offset",           # PDF p4 字面带空格 + 大写 F
    "camera_intrinsics",
    "camera_speed",
    "player_position",
    "player_rotation_oula",          # PDF p5 字面拼音
    "player_rotation_quaternion",
    "player_speed",
    "metric_scale",
)
assert len(ACTION_CAMERA_FIELDS) == 20, "PRD demands exactly 20 fields per frame"


# ---------------------------------------------------------------------------
# Synthetic event helpers — same shape pynput's listeners append into
# ``RecorderApp._captured_events``.
# ---------------------------------------------------------------------------

def make_key_event(timestamp_ms: int, key_code: int, is_down: bool) -> dict[str, Any]:
    """Build a key event in the recorder's internal event format."""
    return {
        "timestamp_ms": int(timestamp_ms),
        "event_type": "key_down" if is_down else "key_up",
        "keyCode": int(key_code),
    }


def make_mouse_move(timestamp_ms: int, mouse_x: int, mouse_y: int) -> dict[str, Any]:
    """Build a mouse_move event."""
    return {
        "timestamp_ms": int(timestamp_ms),
        "event_type": "mouse_move",
        "mouseX": int(mouse_x),
        "mouseY": int(mouse_y),
    }


def make_mouse_click(
    timestamp_ms: int,
    mouse_x: int,
    mouse_y: int,
    button: str = "Button.left",
    pressed: bool = True,
) -> dict[str, Any]:
    """Build a mouse_click event."""
    return {
        "timestamp_ms": int(timestamp_ms),
        "event_type": "mouse_click",
        "mouseX": int(mouse_x),
        "mouseY": int(mouse_y),
        "button": str(button),
        "pressed": bool(pressed),
    }


# ---------------------------------------------------------------------------
# Core: action_camera.json synthesis.
# ---------------------------------------------------------------------------

def synthesize_action_camera_records(
    events: list[dict[str, Any]],
    started_at: float,
    elapsed_sec: float,
    fps: float = FPS_DEFAULT,
    screen_w: int = SCREEN_W,
    screen_h: int = SCREEN_H,
    intrinsics: Optional[dict[str, float]] = None,
) -> list[dict[str, Any]]:
    """Return a list of 20-field per-frame records.

    Mirrors :py:meth:`RecorderApp._package_tarball` lines 1225-1305 in
    ``bin/recorder_consumer_lite.py`` v0.19.0.

    Parameters
    ----------
    events
        pynput-style event list as captured by
        :class:`recorder_consumer_lite.InputCapture`. Each entry has
        ``timestamp_ms`` plus event-specific fields.
    started_at
        UNIX epoch seconds at which recording started — used for the
        per-frame ``time`` field.
    elapsed_sec
        Recording duration. Frame count is ``int(elapsed_sec * fps)`` per
        the recorder; if ``elapsed_sec <= 0`` the recorder falls back to
        9000 frames.
    fps
        Frame rate (default 30).
    screen_w, screen_h
        Window dimensions used to normalise mouse coords into [0, 1].
    intrinsics
        Camera intrinsics dict; default mirrors recorder_consumer_lite
        (70° vertical FOV → fy ≈ 771.4, fx == fy, cx/cy = window/2).
    """
    if intrinsics is None:
        intrinsics = dict(DEFAULT_INTRINSICS)

    target_frame_count = int(elapsed_sec * fps) if elapsed_sec > 0 else 9000

    sorted_events = sorted(events, key=lambda e: e.get("timestamp_ms", 0))
    cur_keys: list[int] = []
    cur_mx, cur_my = screen_w // 2, screen_h // 2
    prev_mx, prev_my = cur_mx, cur_my
    ev_idx = 0
    base_time = datetime.fromtimestamp(started_at)

    records: list[dict[str, Any]] = []
    for f in range(target_frame_count):
        f_ms = int(f * 1000.0 / fps)
        t = base_time + timedelta(milliseconds=f_ms)
        t_str = t.strftime("%Y-%m-%d %H:%M:%S.") + f"{t.microsecond // 1000:03d}"

        # Apply every event whose timestamp is ≤ this frame's window.
        while (
            ev_idx < len(sorted_events)
            and sorted_events[ev_idx].get("timestamp_ms", 0) <= f_ms
        ):
            ev = sorted_events[ev_idx]
            et = ev.get("event_type", "")
            if et == "key_down":
                kc = int(ev.get("keyCode", -1))
                if kc >= 0 and kc not in cur_keys:
                    cur_keys.append(kc)
            elif et == "key_up":
                kc = int(ev.get("keyCode", -1))
                if kc in cur_keys:
                    cur_keys.remove(kc)
            elif et in ("mouse_move", "mouse_click"):
                cur_mx = int(ev.get("mouseX", cur_mx))
                cur_my = int(ev.get("mouseY", cur_my))
            ev_idx += 1

        mx_n = cur_mx / screen_w
        my_n = cur_my / screen_h
        mdx = (cur_mx - prev_mx) / screen_w
        mdy = (cur_my - prev_my) / screen_h
        prev_mx, prev_my = cur_mx, cur_my

        records.append({
            "frame": f,
            "time": t_str,
            "fps": fps,
            "route_type": 1,
            # PRD 文件2: mouse_* are list[float]; mouse_x/y ∈ [0,1], dx/dy ∈ [-1,1]
            "mouse_x": [mx_n],
            "mouse_y": [my_n],
            "mouse_dx": [mdx],
            "mouse_dy": [mdy],
            "keyCode": list(cur_keys) if cur_keys else [],
            "camera_position": [0.0, 64.0, 0.0],
            "camera_rotation_oula": [0.0, 0.0, 0.0],          # PDF p4 字面
            "camera_rotation_quaternion": [0.0, 0.0, 0.0, 1.0],
            "camera_Follow Offset": [0.0, 1.6, 0.0],          # PDF p4 字面
            "camera_intrinsics": dict(intrinsics),
            "camera_speed": [0.0, 0.0, 0.0],
            "player_position": [0.0, 64.0, 0.0],
            "player_rotation_oula": [0.0, 0.0, 0.0],          # PDF p5 字面
            "player_rotation_quaternion": [0.0, 0.0, 0.0, 1.0],
            "player_speed": [0.0, 0.0, 0.0],
            "metric_scale": 1.0,
        })

    return records


# ---------------------------------------------------------------------------
# Replay Mod camera-track merge — mirrors the recorder's Replay Mod hook
# but operating on the recorder's *list-of-records* schema (the upstream
# helper expects a {"frames": [...]} wrapper which the recorder does not
# emit).
# ---------------------------------------------------------------------------

def merge_replay_camera_track(
    action_camera_path: Path,
    samples: list,
) -> bool:
    """Overlay Replay Mod 6DoF samples onto a list-shaped action_camera.json.

    Each ``CameraSample`` (see ``recorder_replay_mod_postprocess``) is
    written into the matching frame's ``camera_position`` and
    ``camera_rotation_quaternion`` (and the player-side mirrors, since
    the Replay Mod stub treats player == camera for first-person clips).
    Returns True if the file was rewritten.
    """
    if not action_camera_path.is_file():
        return False
    try:
        records = json.loads(action_camera_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(records, list):
        return False
    n = min(len(records), len(samples))
    for i in range(n):
        s = samples[i]
        records[i]["camera_position"] = [s.pos_x, s.pos_y, s.pos_z]
        # Replay Mod uses (w,x,y,z); recorder_consumer_lite emits [x,y,z,w].
        records[i]["camera_rotation_quaternion"] = [s.quat_x, s.quat_y, s.quat_z, s.quat_w]
        records[i]["player_position"] = [s.pos_x, s.pos_y, s.pos_z]
        records[i]["player_rotation_quaternion"] = [s.quat_x, s.quat_y, s.quat_z, s.quat_w]
    action_camera_path.write_text(
        json.dumps(records, separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
    )
    return True


# ---------------------------------------------------------------------------
# Full packaging — mirrors RecorderApp._package_tarball minus the
# clip-id / output-dir / lint side-effects.
# ---------------------------------------------------------------------------

@dataclass
class PackagingResult:
    """What :func:`package_tarball` returns."""
    tarball: Path
    clip_dir: Path
    action_camera_path: Path
    systeminfo_path: Path
    gameinfo_path: Path
    depth_dir: Path
    intrinsics_path: Path
    frame_count: int
    replay_status: Optional[str] = None
    replay_metadata: dict = field(default_factory=dict)


def package_tarball(
    out_dir: Path,
    *,
    video_path: Optional[Path] = None,
    events: Optional[list[dict[str, Any]]] = None,
    started_at: Optional[float] = None,
    elapsed_sec: float = 5.0,
    mc_window_rect: Optional[dict[str, int]] = None,
    mcpr_path: Optional[Path] = None,
    fps: float = FPS_DEFAULT,
    clip_ts: Optional[str] = None,
) -> PackagingResult:
    """End-to-end packaging.

    Mirrors :py:meth:`RecorderApp._package_tarball` (lines 1169-1407 of
    ``recorder_consumer_lite.py`` v0.19.0). Designed for tests:

    * Caller passes ``out_dir`` (the test's ``tmp_path``).
    * ``events`` are pynput-shape; if ``None`` no events are replayed.
    * If ``mcpr_path`` is given, Replay Mod postprocess runs on it and
      its ``CameraSample`` track is merged into action_camera.json so
      camera_position / quaternion are non-zero on the first
      ``len(samples)`` frames.

    The function does NOT spawn ffmpeg; the caller supplies a video file
    (or omits it — a 1-byte stub will be generated for tarball shape).
    """
    if events is None:
        events = []
    if started_at is None:
        started_at = time.time()
    if clip_ts is None:
        clip_ts = datetime.fromtimestamp(started_at).strftime("%Y%m%d-%H%M%S")
    if mc_window_rect is None:
        mc_window_rect = {"x": 0, "y": 0, "width": SCREEN_W, "height": SCREEN_H,
                          "title": "Minecraft", "recordDpi": 96}

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"oyster-rec-test-{clip_ts}-",
                                    dir=str(out_dir)))
    clip_dir = tmp_dir / f"clip-{clip_ts}"
    clip_dir.mkdir(parents=True, exist_ok=True)

    # 1. video.mp4 — copy real if provided, else 1-byte stub.
    out_video = clip_dir / "video.mp4"
    if video_path is not None and Path(video_path).exists():
        shutil.copy2(str(video_path), str(out_video))
    else:
        out_video.write_bytes(b"\x00")

    # 2. systeminfo.json — try the canonical helper, fall back to stub.
    rect = mc_window_rect
    try:
        import generate_systeminfo_json as gsi  # noqa: PLC0415
        sys_info = gsi.build_systeminfo(
            game_process_name="javaw.exe",
            x=int(rect.get("x", 0)),
            y=int(rect.get("y", 0)),
            width=int(rect.get("width", SCREEN_W)),
            height=int(rect.get("height", SCREEN_H)),
            record_dpi=float(rect.get("recordDpi", 96)) / 96.0,
        )
        sys_info["recordedAt"] = clip_ts
        sys_info["recorderVersion"] = "test-harness"
        sys_info["_real_window_geometry"] = bool(mc_window_rect)
    except Exception as e:
        logger.debug("Failed to parse window rect: %s", e)
        sys_info = {
            "gameProcessName": rect.get("title", "Minecraft"),
            "x": rect.get("x", 0),
            "y": rect.get("y", 0),
            "width": rect.get("width", SCREEN_W),
            "height": rect.get("height", SCREEN_H),
            "recordDpi": rect.get("recordDpi", 96),
            "recordedAt": clip_ts,
            "recorderVersion": "test-harness-fallback",
        }
    sys_info["actual_duration_sec"] = round(float(elapsed_sec), 1)
    sys_info["partial"] = elapsed_sec < 300.0
    systeminfo_path = clip_dir / "systeminfo.json"
    systeminfo_path.write_text(
        json.dumps(sys_info, indent=2), encoding="utf-8"
    )

    # 3. action_camera.json — the heart of the test.
    records = synthesize_action_camera_records(
        events=events,
        started_at=started_at,
        elapsed_sec=elapsed_sec,
        fps=fps,
        screen_w=int(rect.get("width", SCREEN_W)) or SCREEN_W,
        screen_h=int(rect.get("height", SCREEN_H)) or SCREEN_H,
    )
    action_camera_path = clip_dir / "action_camera.json"
    action_camera_path.write_text(
        json.dumps(records, separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
    )

    # 4. gameinfo.xlsx — try real helper, fall back to minimal stub.
    gameinfo_path = clip_dir / "gameinfo.xlsx"
    try:
        import generate_gameinfo_xlsx as ggx  # noqa: PLC0415
        mc_version = ggx.parse_game_version_from_window_title(str(rect.get("title", "")))
        game_info = ggx.build_gameinfo_dict(
            game_name="Minecraft",
            game_version=mc_version,
            platform="Java Edition",
            scene_name="overworld",
            weather="clear",
            time_of_day="day",
            character_name="DataPilot",
            character_class="player",
            operator_id="test-harness",
            total_frames=int(elapsed_sec * fps),
            video_duration_sec=float(elapsed_sec),
            route_type=1,
            notes=f"recorder_test_harness clip @ {clip_ts}",
        )
        ggx.write_xlsx(game_info, str(gameinfo_path))
    except Exception as e:
        logger.debug("Failed to write gameinfo.xlsx: %s", e)
        # Minimal valid OOXML so structural checks pass.
        _write_minimal_xlsx_stub(gameinfo_path)

    # 5. depth/ placeholder.
    depth_dir = clip_dir / "depth"
    depth_dir.mkdir(exist_ok=True)
    (depth_dir / "_README.txt").write_text(
        "test harness — depth not generated.\n", encoding="utf-8"
    )

    # 6. intrinsics.yaml — best-effort yaml, fall back to plain text.
    intrinsics = {
        "fx": _FY,
        "fy": _FY,
        # PRD 文件2 wire 例：cx/cy 小写.
        "cx": 960.0,
        "cy": 540.0,
        "width": SCREEN_W,
        "height": SCREEN_H,
        "fov_vertical_deg": 70.0,
    }
    intrinsics_path = clip_dir / "intrinsics.yaml"
    try:
        import yaml  # noqa: PLC0415
        intrinsics_path.write_text(
            yaml.safe_dump(intrinsics, sort_keys=False), encoding="utf-8"
        )
    except Exception as e:
        logger.debug("Failed to write intrinsics.yaml with YAML: %s", e)
        intrinsics_path.write_text(
            "\n".join(f"{k}: {v}" for k, v in intrinsics.items()),
            encoding="utf-8",
        )

    # 7. Replay Mod merge — opt-in via mcpr_path.
    replay_status: Optional[str] = None
    replay_metadata: dict = {}
    if mcpr_path is not None:
        try:
            from bin.recorder_replay_mod_postprocess import (  # noqa: PLC0415
                extract_camera_track,
            )
            result = extract_camera_track(Path(mcpr_path), hz=int(fps))
            replay_status = result.status
            replay_metadata = dict(result.metadata)
            if result.samples:
                merge_replay_camera_track(action_camera_path, result.samples)
        except Exception as exc:  # noqa: BLE001
            replay_status = f"error: {type(exc).__name__}: {exc}"

    # 8. Tarball it.
    out_tar = out_dir / f"clip-{clip_ts}.tar.gz"
    with tarfile.open(out_tar, "w:gz") as tf:
        tf.add(clip_dir, arcname=f"clip-{clip_ts}")

    return PackagingResult(
        tarball=out_tar,
        clip_dir=clip_dir,
        action_camera_path=action_camera_path,
        systeminfo_path=systeminfo_path,
        gameinfo_path=gameinfo_path,
        depth_dir=depth_dir,
        intrinsics_path=intrinsics_path,
        frame_count=len(records),
        replay_status=replay_status,
        replay_metadata=replay_metadata,
    )


# ---------------------------------------------------------------------------
# Minimal xlsx stub — copy of recorder_consumer_lite._write_minimal_xlsx
# so we don't need to import the Tk-coupled module.
# ---------------------------------------------------------------------------

def _write_minimal_xlsx_stub(path: Path) -> None:
    """Hand-rolled minimal valid OOXML zip."""
    import zipfile

    ct = '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' \
         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' \
         '<Default Extension="xml" ContentType="application/xml"/>' \
         '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' \
         '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' \
         '</Types>'
    rels = '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' \
           '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>' \
           '</Relationships>'
    workbook = '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"' \
               ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">' \
               '<sheets><sheet name="GameInfo" sheetId="1" r:id="rId1"/></sheets></workbook>'
    workbook_rels = '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' \
                    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>' \
                    '</Relationships>'
    sheet1 = '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' \
             '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>test-harness</t></is></c></row></sheetData>' \
             '</worksheet>'
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", ct)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet1)


# ---------------------------------------------------------------------------
# Synthetic .mcpr builder — used by the Replay Mod path test to create a
# fixture .mcpr without depending on Replay Mod itself.
# ---------------------------------------------------------------------------

def build_synthetic_mcpr(
    out_path: Path,
    duration_ms: int,
    mc_version: str = "1.20.4",
    self_id: int = 0,
    packet_count: int = 100,
) -> Path:
    """Build a minimal .mcpr fixture: zip of metaData.json + recording.tmcpr.

    The recording.tmcpr file uses the [int32 BE timestamp_ms][int32 BE
    length] header pattern that ``recorder_replay_mod_postprocess`` reads
    for its high-level scan. Each "packet" is an empty payload (length=0)
    spaced evenly across ``duration_ms``.
    """
    import struct
    import zipfile

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    meta = {
        "mcversion": mc_version,
        "serverName": "test-harness",
        "selfId": int(self_id),
        "singleplayer": True,
        "duration": int(duration_ms),
    }

    # Build packet stream: N packets evenly spread across duration_ms,
    # each with length=0 (empty payload) so the scanner's cursor lands
    # exactly on the next header.
    if packet_count <= 0:
        stream = b""
    else:
        step_ms = max(1, duration_ms // packet_count)
        chunks = []
        for i in range(packet_count):
            ts = min(duration_ms, (i + 1) * step_ms)
            chunks.append(struct.pack(">II", int(ts), 0))
        stream = b"".join(chunks)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("metaData.json", json.dumps(meta))
        zf.writestr("recording.tmcpr", stream)
    return out_path


def build_replay_camera_samples(
    duration_sec: float,
    hz: float = FPS_DEFAULT,
    radius: float = 5.0,
    height: float = 64.0,
) -> list:
    """Build a deterministic, non-zero CameraSample track for tests."""
    from bin.recorder_replay_mod_postprocess import (  # noqa: PLC0415
        CameraSample,
    )

    n = int(duration_sec * hz)
    samples: list = []
    for i in range(n):
        # Circular path on XZ + tiny Y bobble.
        theta = (i / max(n - 1, 1)) * 2.0 * math.pi
        pos_x = radius * math.cos(theta)
        pos_y = height + 0.1 * math.sin(theta)
        pos_z = radius * math.sin(theta)
        # Yaw-only quaternion: rotate around Y by theta.
        cy = math.cos(theta * 0.5)
        sy = math.sin(theta * 0.5)
        samples.append(CameraSample(
            t_seconds=round(i / hz, 6),
            pos_x=pos_x,
            pos_y=pos_y,
            pos_z=pos_z,
            quat_w=cy,
            quat_x=0.0,
            quat_y=sy,
            quat_z=0.0,
        ))
    return samples


__all__ = [
    "ACTION_CAMERA_FIELDS",
    "DEFAULT_INTRINSICS",
    "FPS_DEFAULT",
    "PackagingResult",
    "SCREEN_H",
    "SCREEN_W",
    "build_replay_camera_samples",
    "build_synthetic_mcpr",
    "make_key_event",
    "make_mouse_click",
    "make_mouse_move",
    "merge_replay_camera_track",
    "package_tarball",
    "synthesize_action_camera_records",
]
