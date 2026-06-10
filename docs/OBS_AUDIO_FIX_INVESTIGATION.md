# OBS Audio Fix Investigation

## Summary

`/Users/howardli/Downloads/minipc1-5月16日-完整-885MB-session/audio.flac` is a valid 300.003 second stereo FLAC, but every decoded sample is zero. `sox stats` reports DC offset 0.000000, min/max 0.000000, and RMS level `-inf`; `ffmpeg silencedetect` reports one silence interval from 0 to 300.002902 seconds.

The recorder is creating an OBS video source, but for the default Minecraft Java path (`javaw.exe`) it does not attach any live OBS audio source. The embedded recorder resolves `javaw` to WGC, disables WGC/Application Audio Capture with `capture_audio = false`, and then detaches the monitor-only Desktop Audio source because WGC is not monitor capture.

## Current OBS Audio Source Code

### Backend in use: embedded OBS recorder

The packaged recorder uses `vendor/recorder/src/record/obs_embedded_recorder.rs` for embedded libobs capture. The older/alternate OBS websocket backend still has `capture_audio: true`, but it is not the embedded OBS code path that produced the minipc session.

`vendor/recorder/src/record/obs_socket_recorder.rs` still configures app audio for its `game_capture` input:

```rust
let input_settings = {
    serde_json::json!({
        "capture_mode": "window",
        "window": get_obs_window_encoding(hwnd, game_exe),
        "priority": 2 /* WINDOW_PRIORITY_EXE */,
        "capture_audio": true,
    })
};
```

### Embedded OBS constants

`vendor/recorder/src/record/obs_embedded_recorder.rs` defines separate video capture sources and monitor-only WASAPI audio sources:

```rust
const OWL_WINDOW_CAPTURE_NAME: &str = "owl_window_capture";
const OWL_GAME_CAPTURE_NAME: &str = "owl_game_capture";
const OWL_MONITOR_CAPTURE_NAME: &str = "owl_monitor_capture";
const OWL_WGC_CAPTURE_NAME: &str = "owl_wgc_capture";
const WGC_CAPTURE_SOURCE_ID: &str = "window_capture";

const OWL_DESKTOP_AUDIO_NAME: &str = "owl_desktop_audio";
const OWL_MICROPHONE_AUDIO_NAME: &str = "owl_microphone_audio";
const WASAPI_OUTPUT_CAPTURE_ID: &str = "wasapi_output_capture";
const WASAPI_INPUT_CAPTURE_ID: &str = "wasapi_input_capture";
```

### Capture mode resolution for Minecraft

`vendor/recorder/crates/constants/src/lib.rs` whitelists Minecraft Java as `javaw` and `minecraft`:

```rust
// Minecraft (WARNING: javaw.exe is used by many Java apps, not just Minecraft)
// This may cause false positives for other Java applications
"javaw",
"minecraft",
```

`vendor/recorder/src/config.rs` defaults `GameConfig` to `CaptureMode::Auto` and `use_window_capture: true`. For any non-`test_game` executable not listed in `KNOWN_HOOK_REQUIRED_GAMES`, Auto resolves to WGC:

```rust
impl Default for GameConfig {
    fn default() -> Self {
        Self {
            use_window_capture: true,
            capture_mode: CaptureMode::default(),
        }
    }
}

pub fn effective_capture_mode(&self, game_exe_stem: &str) -> EffectiveCaptureMode {
    match self.capture_mode {
        CaptureMode::Monitor => EffectiveCaptureMode::Monitor,
        CaptureMode::GameHook => EffectiveCaptureMode::GameHook,
        CaptureMode::Wgc => EffectiveCaptureMode::Wgc,
        CaptureMode::Auto => {
            if game_exe_stem == TEST_GAME_EXE_STEM {
                return EffectiveCaptureMode::Monitor;
            }
            if constants::KNOWN_HOOK_REQUIRED_GAMES
                .iter()
                .any(|g| *g == game_exe_stem)
            {
                return EffectiveCaptureMode::GameHook;
            }
            if !self.use_window_capture {
                return EffectiveCaptureMode::GameHook;
            }
            EffectiveCaptureMode::Wgc
        }
    }
}
```

`KNOWN_HOOK_REQUIRED_GAMES` is currently empty, so `javaw.exe` resolves to WGC.

### Embedded recorder audio routing decision

At recording start, embedded OBS decides whether to attach WASAPI Desktop Audio before calling `prepare_source`:

```rust
let effective_mode = request.game_config.effective_capture_mode(&game_exe_stem);

let monitors_available = !MonitorCaptureSourceBuilder::get_monitors()
    .unwrap_or_default()
    .is_empty();
let use_monitor_capture_audio =
    should_attach_monitor_audio(effective_mode, monitors_available);

let (source, monitor_info) = prepare_source(/* ... */)?;

scene.set_to_channel(0)?;
scene.fit_source_to_screen(&source)?;

if use_monitor_capture_audio {
    self.attach_monitor_capture_audio(request.record_microphone)
        .wrap_err("Failed to attach WASAPI audio sources for monitor capture")?;
} else {
    self.detach_monitor_capture_audio();
}
```

The helper only returns true for real Monitor capture:

```rust
fn should_attach_monitor_audio(
    mode: crate::config::EffectiveCaptureMode,
    monitors_available: bool,
) -> bool {
    matches!(mode, crate::config::EffectiveCaptureMode::Monitor) && monitors_available
}
```

### Monitor capture is the only path with Desktop Audio Output Capture

The only explicit Desktop Audio Output Capture source is created inside `attach_monitor_capture_audio`, using OBS `wasapi_output_capture` on global channel 1:

```rust
let mut desktop_settings = self.obs_context.data()?;
desktop_settings.set_string("device_id", "default")?;
let desktop = ObsSourceRef::new(
    WASAPI_OUTPUT_CAPTURE_ID,
    OWL_DESKTOP_AUDIO_NAME,
    Some(desktop_settings),
    None,
    runtime.clone(),
)?;
set_output_source_on_channel(&runtime, DESKTOP_AUDIO_CHANNEL, Some(&desktop))?;
self.desktop_audio_source = Some(desktop);
```

Microphone capture is opt-in only and uses `wasapi_input_capture` on channel 2.

### WGC/GameHook/Application Audio Capture is globally disabled

Inside `prepare_source`, audio is hardcoded off:

```rust
// Audio capture disabled to save resources and avoid the WASAPI audio
// companion infinite retry loop bug on second recording. With audio disabled:
// - Saves ~1-3% CPU, 5-15 MB memory, and ~15% disk space
// - Eliminates the second recording crash (no WASAPI companion = no retry loop)
// - Recordings are video-only (no game audio)
let capture_audio = false;
```

That value is passed into every non-monitor source that would otherwise create an OBS Application Audio Capture stream:

```rust
// Window-capture fallback
.set_capture_audio(capture_audio)?

// GameHook
.set_capture_audio(capture_audio)?

// WGC
settings.set_bool("capture_audio", capture_audio)?;
```

The WGC branch comment says `capture_audio` would produce the target window's Application Audio Capture stream:

```rust
// `capture_audio` - WGC's own audio tap. Like game_capture,
// this produces an "Application Audio Capture" stream for
// the target window.
settings.set_bool("capture_audio", capture_audio)?;
```

Because `capture_audio` is false, this source is not actually enabled.

### Recorder metadata also says audio is off

`vendor/recorder/src/record/metadata_writer.rs` writes recorder metadata with `record_audio: false`:

```rust
capture_method: "game_capture".to_string(),
record_audio: false,
audio_bitrate: 128,
```

That metadata is consistent with the silent result, although the capture method field is stale for WGC.

## Why The Minipc Session Is Silent

For a normal Minecraft Java foreground window:

1. The process name is `javaw.exe`.
2. The recorder strips `.exe`, lowercases it to `javaw`, and accepts it because `javaw` is in `GAME_WHITELIST`.
3. Default `GameConfig` is `capture_mode = Auto` and `use_window_capture = true`.
4. `KNOWN_HOOK_REQUIRED_GAMES` is empty, so Auto resolves `javaw` to `EffectiveCaptureMode::Wgc`.
5. `should_attach_monitor_audio(Wgc, true)` returns false, so the embedded recorder calls `detach_monitor_capture_audio()` instead of creating `wasapi_output_capture`.
6. `prepare_source` creates/updates the WGC `window_capture` source with `method = 2`, but sets `capture_audio = false`.
7. OBS records a video source plus an AAC audio encoder, but no live audio source feeds the mix. The downstream `ffmpeg -vn -c:a flac` step faithfully extracts the silent AAC track into `audio.flac`.

This exactly matches the observed artifact: `recording.mp4` has a 300.003 second stereo AAC stream, and `audio.flac` has a 300.003 second stereo FLAC stream, but all samples are zero.

## Proposed Fix

Goal: every recording should attach exactly one game-audio source by default, and microphone capture should remain opt-in.

Recommended implementation path:

1. Replace the hardcoded `let capture_audio = false;` in `prepare_source` with an audio policy that enables Application Audio Capture for WGC, GameHook, and the window-capture fallback. For Minecraft Java, that means `javaw.exe` under Auto/WGC should set:

```rust
settings.set_bool("capture_audio", true)?;
```

and GameHook/window fallback should call:

```rust
.set_capture_audio(true)?
```

2. Keep `should_attach_monitor_audio` true only for real Monitor capture, so Monitor mode continues to use the existing Desktop Audio Output Capture (`wasapi_output_capture`) and WGC/GameHook do not double-record.

3. Keep the existing force-recreate behavior for WGC/GameHook sources. It was added to avoid stale WASAPI process-loopback companions on later recordings; it is still the right mitigation if Application Audio Capture is re-enabled.

4. Add a runtime audio health gate for the first 5 to 10 seconds of each recording or post-stop finalization:
   - If the recording contains an audio stream but RMS is `-inf` / zero samples, mark the session invalid and surface a recorder error.
   - Optionally retry the next session with a fallback policy that attaches global Desktop Audio Output Capture (`wasapi_output_capture`) even for WGC/GameHook. This is less precise because it records all default render-device audio, but it is better than a silent buyer artifact on dedicated minipc collection machines.

5. Add regression tests around the policy:
   - `javaw` + default `GameConfig` resolves to WGC and enables Application Audio Capture.
   - Monitor mode attaches `wasapi_output_capture`.
   - WGC/GameHook do not attach monitor WASAPI when Application Audio Capture is enabled.
   - `record_audio` metadata reflects the actual audio policy instead of always `false`.

Minimal targeted patch shape, for a later Rust change:

```rust
let capture_audio = matches!(
    state.effective_mode,
    crate::config::EffectiveCaptureMode::Wgc
        | crate::config::EffectiveCaptureMode::GameHook
) || monitor_fallback_to_window_capture;
```

The exact helper should be explicit rather than inline, because this is now a buyer-facing data contract: silent audio must be impossible unless the user intentionally selects a video-only mode.

## Validation To Run After The Rust Fix

1. Build the recorder and run a 5 minute Minecraft Java session on minipc1.
2. Confirm OBS logs show either WGC/window `capture_audio=true` or `wasapi_output_capture` attached.
3. Extract audio:

```bash
ffmpeg -y -i recording.mp4 -vn -c:a flac audio.flac
```

4. Verify non-silence:

```bash
sox audio.flac -n stats
ffmpeg -hide_banner -nostats -i audio.flac \
  -af silencedetect=noise=-50dB:duration=1 -f null -
```

Acceptance: min/max are not both 0.000000, RMS is finite, and `silencedetect` does not report one full-session silence interval.
