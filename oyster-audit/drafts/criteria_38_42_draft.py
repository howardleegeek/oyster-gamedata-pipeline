
# ---------------------------------------------------------------------------
# Criterion 38: Audio stream exists in mp4 + AAC codec (ffprobe)
# ---------------------------------------------------------------------------

def _check_audio_stream_aac(d: Path, rpt: LintReport) -> None:
    """Criterion 38: mp4 must contain an audio stream with AAC codec.

    Uses ffprobe to verify the primary video file has at least one audio
    stream whose codec_name is 'aac'.  Missing ffprobe -> strict-aware skip.
    """
    primary = _primary_video(d)
    if primary is None:
        rpt.add(LintResult(
            38, "Audio Stream AAC", False,
            "No primary video (video.mp4 / recording.mp4) found"))
        return

    if not _ffprobe_available():
        rpt.add(_dep_missing(
            38, "Audio Stream AAC",
            "ffprobe unavailable -- cannot verify AAC audio stream"))
        return

    audio_meta = _ffprobe_audio_stream(primary)
    if audio_meta is None:
        rpt.add(LintResult(
            38, "Audio Stream AAC", False,
            f"No audio stream found in {primary.name}",
            {"video": primary.name}))
        return

    codec = audio_meta.get("codec_name", "")
    ok = codec.lower() == "aac"
    rpt.add(LintResult(
        38, "Audio Stream AAC", ok,
        f"audio codec={codec}" + ("" if ok else " (require aac)"),
        {"video": primary.name, "codec": codec,
         "sample_rate": audio_meta.get("sample_rate"),
         "channels": audio_meta.get("channels")}))


# ---------------------------------------------------------------------------
# Criterion 39: Input-to-frame latency measurement file exists + <= 20ms
# ---------------------------------------------------------------------------

def _check_input_frame_latency(d: Path, rpt: LintReport) -> None:
    """Criterion 39: input-to-frame latency file exists and all values <= 20ms.

    Looks for a latency measurement file (latency.json, latency_ms.json,
    or input_latency.json) in the data directory.  Each entry must have
    a numeric latency value <= 20 ms.
    """
    candidates = (
        list(d.glob("latency*.json"))
        + list(d.glob("**/latency*.json"))
        + list(d.glob("input_latency*.json"))
        + list(d.glob("**/input_latency*.json"))
    )
    # Deduplicate while preserving order
    seen: set = set()
    unique: List[Path] = []
    for c in candidates:
        key = c.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(c)

    if not unique:
        rpt.add(LintResult(
            39, "Input-to-Frame Latency", False,
            "No latency measurement file found "
            "(expected latency*.json or input_latency*.json)"))
        return

    lat_path = unique[0]
    try:
        with open(lat_path) as f:
            data = json.load(f)
    except Exception as e:
        rpt.add(LintResult(
            39, "Input-to-Frame Latency", False,
            f"Failed to parse {lat_path.name}: {e}"))
        return

    # Accept formats:
    #   - list of numbers (ms values)
    #   - list of dicts with a "latency_ms" or "latency" key
    #   - dict with a "latencies" / "measurements" / "values" key
    values: List[float] = []

    if isinstance(data, list):
        for item in data:
            if isinstance(item, (int, float)):
                values.append(float(item))
            elif isinstance(item, dict):
                v = item.get("latency_ms") or item.get("latency")
                if v is not None:
                    try:
                        values.append(float(v))
                    except (TypeError, ValueError):
                        pass
    elif isinstance(data, dict):
        for key in ("latencies", "measurements", "values", "data"):
            raw = data.get(key)
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, (int, float)):
                        values.append(float(item))
                    elif isinstance(item, dict):
                        v = item.get("latency_ms") or item.get("latency")
                        if v is not None:
                            try:
                                values.append(float(v))
                            except (TypeError, ValueError):
                                pass
                break  # use first matching key

    if not values:
        rpt.add(LintResult(
            39, "Input-to-Frame Latency", False,
            f"No numeric latency values found in {lat_path.name}"))
        return

    max_lat = max(values)
    ok = max_lat <= 20.0
    rpt.add(LintResult(
        39, "Input-to-Frame Latency", ok,
        (f"{len(values)} samples, max={max_lat:.1f}ms"
         + ("" if ok else f" (require <= 20ms)")),
        {"file": lat_path.name, "samples": len(values),
         "max_ms": round(max_lat, 2),
         "mean_ms": round(sum(values) / len(values), 2)}))


# ---------------------------------------------------------------------------
# Criterion 40: WASD balance -- total_keyboard_events split
#   each direction within 25% +/- 15%  (i.e. 10%-40% each)
# ---------------------------------------------------------------------------

def _check_wasd_balance(d: Path, rpt: LintReport) -> None:
    """Criterion 40: WASD key-event balance.

    Reads action_camera.json and counts keyboard events
    for W, A, S, D directions.  Each direction should account for
    25% +/- 15% of total WASD events (i.e. 10%-40% each).

    If fewer than 20 total WASD events, the check is skipped (PASS with
    note) because the sample is too small to be meaningful.
    """
    data, path = _load_action_camera(d)

    # Count WASD events from action_camera.json frames
    wasd_counts: Dict[str, int] = {"W": 0, "A": 0, "S": 0, "D": 0}

    if isinstance(data, list):
        for frame in data:
            if not isinstance(frame, dict):
                continue
            # Check keys_pressed or keys_held arrays
            for key_field in ("keys_pressed", "keys_held", "keys_down",
                              "active_keys"):
                keys = frame.get(key_field)
                if isinstance(keys, list):
                    for k in keys:
                        ks = str(k).upper()
                        if ks in ("W", "A", "S", "D",
                                  "KEY_W", "KEY_A", "KEY_S", "KEY_D",
                                  "87", "65", "83", "68"):  # VK codes
                            # Map VK codes / KEY_ prefixed to letter
                            letter = ks
                            if ks in ("KEY_W", "87"):
                                letter = "W"
                            elif ks in ("KEY_A", "65"):
                                letter = "A"
                            elif ks in ("KEY_S", "83"):
                                letter = "S"
                            elif ks in ("KEY_D", "68"):
                                letter = "D"
                            wasd_counts[letter] += 1

    total = sum(wasd_counts.values())

    if total < 20:
        rpt.add(LintResult(
            40, "WASD Balance", True,
            f"Only {total} WASD events (insufficient sample, skipping)",
            {"counts": dict(wasd_counts), "total": total}))
        return

    ok = True
    details: Dict[str, Any] = {"counts": dict(wasd_counts), "total": total}
    for direction, count in wasd_counts.items():
        pct = count / total * 100
        details[f"{direction}_pct"] = round(pct, 1)
        if pct < 10.0 or pct > 40.0:
            ok = False

    rpt.add(LintResult(
        40, "WASD Balance", ok,
        (f"W={wasd_counts['W']} A={wasd_counts['A']} "
         f"S={wasd_counts['S']} D={wasd_counts['D']} (total={total})"
         + ("" if ok else " -- one or more directions outside 10%-40%")),
        details))


# ---------------------------------------------------------------------------
# Criterion 41: Stationary % <= 10%
#   No 2+ second windows where mouse_dx/dy=0 AND no keys pressed
# ---------------------------------------------------------------------------

def _check_stationary_pct(d: Path, rpt: LintReport) -> None:
    """Criterion 41: Stationary percentage <= 10%.

    A frame is considered 'stationary' when:
      - mouse_dx == 0 AND mouse_dy == 0
      - AND no keys are pressed (keys_pressed/keys_held is empty or absent)

    We also check for contiguous windows of >= 2 seconds of stationary
    frames.  If the fraction of stationary frames exceeds 10%, FAIL.
    """
    data, path = _load_action_camera(d)
    if data is None or not isinstance(data, list):
        rpt.add(LintResult(
            41, "Stationary Percentage", False,
            "action_camera.json missing or non-list"))
        return

    # Determine FPS from the data or default to 30
    fps = 30.0
    for frame in data:
        if isinstance(frame, dict):
            f = frame.get("fps") or frame.get("frame_rate")
            if f is not None:
                try:
                    fps = float(f)
                    break
                except (TypeError, ValueError):
                    pass

    stationary_count = 0
    max_stationary_run = 0
    current_run = 0

    for frame in data:
        if not isinstance(frame, dict):
            current_run = 0
            continue

        dx = frame.get("mouse_dx", 0)
        dy = frame.get("mouse_dy", 0)
        try:
            dx = float(dx) if dx is not None else 0.0
            dy = float(dy) if dy is not None else 0.0
        except (TypeError, ValueError):
            dx = dy = 0.0

        # Check if any keys are pressed
        keys_pressed = False
        for key_field in ("keys_pressed", "keys_held", "keys_down",
                          "active_keys"):
            keys = frame.get(key_field)
            if isinstance(keys, list) and len(keys) > 0:
                keys_pressed = True
                break

        is_stationary = (abs(dx) < 1e-6 and abs(dy) < 1e-6
                         and not keys_pressed)

        if is_stationary:
            stationary_count += 1
            current_run += 1
            max_stationary_run = max(max_stationary_run, current_run)
        else:
            current_run = 0

    total_frames = len(data)
    if total_frames == 0:
        rpt.add(LintResult(
            41, "Stationary Percentage", False,
            "action_camera.json has 0 frames"))
        return

    stationary_pct = stationary_count / total_frames * 100
    max_stationary_sec = max_stationary_run / fps

    ok = stationary_pct <= 10.0
    rpt.add(LintResult(
        41, "Stationary Percentage", ok,
        (f"stationary={stationary_pct:.1f}% "
         f"({stationary_count}/{total_frames} frames), "
         f"max stationary window={max_stationary_sec:.1f}s"
         + ("" if ok else " (require <= 10%)")),
        {"stationary_frames": stationary_count,
         "total_frames": total_frames,
         "stationary_pct": round(stationary_pct, 1),
         "max_stationary_run_frames": max_stationary_run,
         "max_stationary_seconds": round(max_stationary_sec, 2),
         "fps": fps}))


# ---------------------------------------------------------------------------
# Criterion 42: Clip has < 2 sec frozen frame (consecutive identical frames)
# ---------------------------------------------------------------------------

def _check_frozen_frames(d: Path, rpt: LintReport) -> None:
    """Criterion 42: No frozen frame sequences >= 2 seconds.

    Uses ffmpeg signalstats to detect consecutive frames with identical
    YAVG (luminance average).  If a run of identical frames spans >= 2
    seconds, the check FAILs.

    Falls back to checking action_camera.json for duplicate state if ffmpeg
    is unavailable.
    """
    ffmpeg = shutil.which("ffmpeg")
    primary = _primary_video(d)

    if primary is None:
        rpt.add(LintResult(
            42, "Frozen Frame Detection", False,
            "No primary video found"))
        return

    if not ffmpeg:
        # Fallback: check action_camera.json for duplicate consecutive state
        data, path = _load_action_camera(d)
        if data is None or not isinstance(data, list):
            rpt.add(_dep_missing(
                42, "Frozen Frame Detection",
                "ffmpeg unavailable and action_camera.json missing"))
            return

        # Determine FPS
        fps = 30.0
        for frame in data:
            if isinstance(frame, dict):
                f = frame.get("fps") or frame.get("frame_rate")
                if f is not None:
                    try:
                        fps = float(f)
                        break
                    except (TypeError, ValueError):
                        pass

        # Check for consecutive frames with identical mouse_dx/dy and
        # identical camera_rotation_quaternion (proxy for frozen content)
        max_frozen_run = 0
        current_run = 0
        prev_sig: Optional[Tuple] = None

        for frame in data:
            if not isinstance(frame, dict):
                current_run = 0
                prev_sig = None
                continue

            sig = (
                frame.get("mouse_dx"),
                frame.get("mouse_dy"),
                tuple(frame.get("camera_rotation_quaternion", []))
                if isinstance(frame.get("camera_rotation_quaternion"), list)
                else None,
            )
            if sig == prev_sig:
                current_run += 1
                max_frozen_run = max(max_frozen_run, current_run)
            else:
                current_run = 0
            prev_sig = sig

        frozen_sec = max_frozen_run / fps
        ok = frozen_sec < 2.0
        rpt.add(LintResult(
            42, "Frozen Frame Detection", ok,
            (f"max frozen run={max_frozen_run} frames "
             f"({frozen_sec:.1f}s at {fps} fps)"
             + ("" if ok else " (require < 2s)")),
            {"max_frozen_frames": max_frozen_run,
             "max_frozen_seconds": round(frozen_sec, 2),
             "fps": fps, "method": "action_camera_fallback"}))
        return

    # Primary method: use ffmpeg signalstats on the video
    try:
        proc = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(primary),
             "-vf", "signalstats,metadata=print:file=-",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except Exception as e:
        rpt.add(LintResult(
            42, "Frozen Frame Detection", False,
            f"ffmpeg signalstats failed: {e}"))
        return

    # Parse YAVG values from signalstats output
    yavg_values: List[float] = []
    for line in proc.stdout.splitlines():
        if "lavfi.signalstats.YAVG=" in line:
            try:
                val = float(line.split("=", 1)[1])
                yavg_values.append(val)
            except (TypeError, ValueError):
                pass

    if not yavg_values:
        rpt.add(LintResult(
            42, "Frozen Frame Detection", False,
            "Could not extract YAVG from signalstats output"))
        return

    # Get video FPS for duration calculation
    v_info = _ffprobe_video_stream(primary)
    fps = 30.0
    if v_info and v_info.get("fps"):
        fps = v_info["fps"]

    # Find longest run of consecutive identical YAVG values
    max_frozen_run = 0
    current_run = 0
    prev_yavg: Optional[float] = None

    for yavg in yavg_values:
        if prev_yavg is not None and abs(yavg - prev_yavg) < 1e-6:
            current_run += 1
            max_frozen_run = max(max_frozen_run, current_run)
        else:
            current_run = 0
        prev_yavg = yavg

    frozen_sec = max_frozen_run / fps
    ok = frozen_sec < 2.0
    rpt.add(LintResult(
        42, "Frozen Frame Detection", ok,
        (f"max frozen run={max_frozen_run} frames "
         f"({frozen_sec:.1f}s at {fps:.1f} fps)"
         + ("" if ok else " (require < 2s)")),
        {"max_frozen_frames": max_frozen_run,
         "max_frozen_seconds": round(frozen_sec, 2),
         "fps": round(fps, 1),
         "frames_analyzed": len(yavg_values),
         "method": "ffmpeg_signalstats"}))

