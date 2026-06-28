#!/usr/bin/env python3
"""
audio_loopback.py — Build ffmpeg dshow args for system-audio capture (no mic).

Closes audit gap G279 / B4 (privacy): the recorder previously captured the
default microphone via ``ffmpeg -f dshow -i audio="..."``, which leaked the
user's voice / surroundings. The fix is to record the WASAPI loopback of the
playback device instead — system audio only, no microphone.

WASAPI loopback is exposed by ffmpeg's Windows audio APIs in two ways:

1. ``ffmpeg -f wasapi -i loopback`` (newer ffmpeg builds).
2. ``ffmpeg -f dshow -i audio="virtual-audio-capturer"`` once the user
   installs the Screen Capture Recorder filter (DirectShow loopback shim).

We probe for WASAPI first (no extra install needed) and fall back to DirectShow
microphone capture only when neither WASAPI nor a DirectShow loopback filter
is available — and in that fallback we make a lot of noise so the operator
notices.

This module is stdlib-only (subprocess + dataclasses) so the recorder can
import it without bundling new dependencies.

Public API
----------
* :class:`AudioCaptureMode` — enum-like string constants.
* :class:`AudioCapturePlan` — the resolved plan returned by :func:`plan_audio_capture`.
* :func:`plan_audio_capture` — probe ffmpeg, decide, return a plan.
* :func:`build_ffmpeg_args` — convert a plan into list-of-string ffmpeg args.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Sequence


class AudioCaptureMode:
    """String constants for the audio capture strategies."""

    WASAPI_LOOPBACK = "wasapi_loopback"  # preferred: system audio only
    DSHOW_LOOPBACK_FILTER = "dshow_loopback_filter"  # screen-capture-recorder
    DSHOW_MICROPHONE = "dshow_microphone"  # last resort, privacy WARNING
    NONE = "none"  # ffmpeg unavailable / no device — recorder skips audio


# Common DirectShow loopback / virtual-cable device names installed by the
# usual Windows tooling. We match case-insensitively.
_DSHOW_LOOPBACK_HINTS = (
    "virtual-audio-capturer",
    "stereo mix",
    "stereomix",
    "what u hear",
    "what you hear",
    "vb-audio virtual cable",
    "voicemeeter output",
)


@dataclass(frozen=True)
class AudioCapturePlan:
    """Resolved audio-capture plan for the recorder."""

    mode: str
    device_name: Optional[str] = None
    fallback_used: bool = False
    notes: List[str] = field(default_factory=list)


def _run_ffmpeg(args: Sequence[str], timeout: float = 8.0) -> str:
    """Run ffmpeg, return combined stdout+stderr text. Empty string on failure."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return ""
    try:
        proc = subprocess.run(
            [ffmpeg, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (proc.stdout or "") + (proc.stderr or "")


def _ffmpeg_supports_wasapi() -> bool:
    """Return True if ``ffmpeg -devices`` lists wasapi as a demuxer."""
    out = _run_ffmpeg(["-hide_banner", "-devices"]).lower()
    if not out:
        return False
    # ffmpeg lists devices as e.g. " D  wasapi  Windows Audio Session API".
    return any(
        " wasapi" in line or line.strip().startswith("wasapi")
        for line in out.splitlines()
    )


def _list_dshow_audio_devices() -> List[str]:
    """Return the names of DirectShow audio devices ffmpeg can see.

    Probes ``ffmpeg -f dshow -list_devices true -i dummy``. ffmpeg writes the
    device list to stderr and exits non-zero, which is fine — we just parse
    whatever came back.
    """
    out = _run_ffmpeg(["-hide_banner", "-f", "dshow", "-list_devices", "true", "-i", "dummy"])
    if not out:
        return []
    devices: List[str] = []
    in_audio_section = False
    for raw in out.splitlines():
        line = raw.strip()
        lower = line.lower()
        if "directshow audio devices" in lower:
            in_audio_section = True
            continue
        if "directshow video devices" in lower:
            in_audio_section = False
            continue
        if not in_audio_section:
            continue
        # Lines look like:  [dshow @ 0x...]  "Microphone (Realtek...)"
        if '"' in line:
            start = line.find('"')
            end = line.find('"', start + 1)
            if end > start:
                devices.append(line[start + 1 : end])
    return devices


def _pick_dshow_loopback(devices: Sequence[str]) -> Optional[str]:
    """Return the first DirectShow device that looks like a loopback / virtual cable."""
    for d in devices:
        low = d.lower()
        for hint in _DSHOW_LOOPBACK_HINTS:
            if hint in low:
                return d
    return None


def plan_audio_capture(prefer_wasapi: bool = True) -> AudioCapturePlan:
    """Probe ffmpeg and return the chosen audio-capture plan.

    Decision order:

    1. If ``prefer_wasapi`` and ffmpeg lists ``wasapi`` as a device, use
       :data:`AudioCaptureMode.WASAPI_LOOPBACK`. This is the privacy-safe
       default — it captures the playback render endpoint, never the mic.
    2. Else, if a DirectShow virtual-loopback filter (Screen Capture Recorder,
       Stereo Mix, VB-Cable, etc.) is enumerated, use
       :data:`AudioCaptureMode.DSHOW_LOOPBACK_FILTER`.
    3. Else, fall back to :data:`AudioCaptureMode.DSHOW_MICROPHONE` and record
       a loud warning in :attr:`AudioCapturePlan.notes` — operator must
       acknowledge the privacy risk via the consent screen.
    """
    notes: List[str] = []

    if prefer_wasapi and _ffmpeg_supports_wasapi():
        return AudioCapturePlan(
            mode=AudioCaptureMode.WASAPI_LOOPBACK,
            device_name=None,
            fallback_used=False,
            notes=["wasapi loopback detected — system audio only, no mic"],
        )
    notes.append("wasapi loopback unavailable in this ffmpeg build")

    devices = _list_dshow_audio_devices()
    loopback = _pick_dshow_loopback(devices)
    if loopback:
        return AudioCapturePlan(
            mode=AudioCaptureMode.DSHOW_LOOPBACK_FILTER,
            device_name=loopback,
            fallback_used=False,
            notes=notes + [f"using dshow loopback filter: {loopback}"],
        )
    notes.append("no dshow loopback filter (screen-capture-recorder/stereo mix) found")

    if not devices:
        # Nothing to record (no ffmpeg, or non-Windows host). Recorder will
        # disable the audio track entirely.
        notes.append("no audio devices visible — disabling audio track")
        return AudioCapturePlan(
            mode=AudioCaptureMode.NONE,
            device_name=None,
            fallback_used=True,
            notes=notes,
        )

    # Last resort: dshow mic. Caller MUST gate on user consent.
    mic = devices[0]
    notes.append("FALLBACK: capturing dshow microphone — privacy WARNING, requires consent")
    return AudioCapturePlan(
        mode=AudioCaptureMode.DSHOW_MICROPHONE,
        device_name=mic,
        fallback_used=True,
        notes=notes,
    )


def build_ffmpeg_args(plan: AudioCapturePlan) -> List[str]:
    """Convert a plan into the ``-f ... -i ...`` slice for an ffmpeg command.

    The returned list is ready to be ``cmd.extend(...)``-ed onto an existing
    ffmpeg argv. It does NOT include codec / output flags — only the input
    declaration for the audio stream.
    """
    if plan.mode == AudioCaptureMode.WASAPI_LOOPBACK:
        # ffmpeg accepts "loopback" as a magic device name on wasapi to capture
        # the default render endpoint.
        return ["-f", "wasapi", "-i", "loopback"]

    if plan.mode == AudioCaptureMode.DSHOW_LOOPBACK_FILTER:
        if not plan.device_name:
            raise ValueError("dshow_loopback_filter plan missing device_name")
        return ["-f", "dshow", "-i", f"audio={plan.device_name}"]

    if plan.mode == AudioCaptureMode.DSHOW_MICROPHONE:
        if not plan.device_name:
            raise ValueError("dshow_microphone plan missing device_name")
        return ["-f", "dshow", "-i", f"audio={plan.device_name}"]

    if plan.mode == AudioCaptureMode.NONE:
        # No audio source — caller should also drop ``-c:a`` from the command.
        return []

    raise ValueError(f"unknown audio capture mode: {plan.mode!r}")


def _cli(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Plan ffmpeg audio capture (loopback first).")
    p.add_argument(
        "--no-wasapi",
        action="store_true",
        help="Skip wasapi probe (force dshow path; testing only).",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON instead of human text.")
    args = p.parse_args(argv)

    plan = plan_audio_capture(prefer_wasapi=not args.no_wasapi)
    payload = {
        "mode": plan.mode,
        "device_name": plan.device_name,
        "fallback_used": plan.fallback_used,
        "notes": plan.notes,
        "ffmpeg_args": build_ffmpeg_args(plan),
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"mode: {plan.mode}")
        if plan.device_name:
            print(f"device: {plan.device_name}")
        print(f"fallback_used: {plan.fallback_used}")
        for n in plan.notes:
            print(f"  - {n}")
        print(f"ffmpeg args: {' '.join(payload['ffmpeg_args'])}")

    # Exit code 0 always — recorder reads JSON and decides what to do with
    # a fallback plan. CI smoke tests check fields, not exit code.
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
