#!/usr/bin/env python3
"""
mp4_faststart.py — Splice ``-movflags=+faststart`` into ffmpeg command lists.

Closes audit gap G282 / D2: when ffmpeg writes an MP4, the ``moov`` atom
defaults to the end of the file. If the recorder crashes mid-record, the
container is unplayable because the index is missing. ``+faststart`` moves
the moov atom to the front so a partial file remains seekable.

The :func:`extend_ffmpeg_cmd` helper inserts the flag immediately before the
output path argument and is idempotent — calling it twice is a no-op.

Usage (programmatic)::

    from mp4_faststart import extend_ffmpeg_cmd

    cmd = ["ffmpeg", "-y", "-i", "in.mkv", "out.mp4"]
    cmd = extend_ffmpeg_cmd(cmd)
    # -> ["ffmpeg", "-y", "-i", "in.mkv", "-movflags", "+faststart", "out.mp4"]
"""

from __future__ import annotations

import sys
from typing import List, Sequence

# Output container extensions that benefit from +faststart.
_FASTSTART_EXTS = (".mp4", ".m4v", ".m4a", ".mov", ".3gp", ".3g2")

# Flags that take an argument we should skip when locating the output path.
# (ffmpeg has many; this list covers the recorder's common surface.)
_FLAGS_WITH_ARG = {
    "-i",
    "-c",
    "-c:v",
    "-c:a",
    "-c:s",
    "-codec",
    "-codec:v",
    "-codec:a",
    "-b",
    "-b:v",
    "-b:a",
    "-r",
    "-s",
    "-pix_fmt",
    "-vf",
    "-af",
    "-filter:v",
    "-filter:a",
    "-filter_complex",
    "-map",
    "-metadata",
    "-f",
    "-t",
    "-ss",
    "-to",
    "-preset",
    "-crf",
    "-tune",
    "-profile:v",
    "-level",
    "-g",
    "-keyint_min",
    "-bf",
    "-threads",
    "-thread_type",
    "-movflags",
    "-fflags",
    "-loglevel",
    "-hide_banner",
    "-stats",
    "-progress",
    "-aspect",
    "-ar",
    "-ac",
    "-acodec",
    "-vcodec",
    "-vsync",
    "-async",
    "-bsf:v",
    "-bsf:a",
    "-x264-params",
    "-x264opts",
    "-hwaccel",
    "-hwaccel_device",
    "-init_hw_device",
    "-filter_hw_device",
    "-rtbufsize",
    "-probesize",
    "-analyzeduration",
}


def _is_output_path(arg: str) -> bool:
    """Return True if ``arg`` looks like an output media path."""
    if not arg or arg.startswith("-"):
        return False
    lower = arg.lower()
    return lower.endswith(_FASTSTART_EXTS)


def _find_output_index(cmd: Sequence[str]) -> int:
    """Locate the index of the trailing output path in an ffmpeg command list.

    We walk forward, skipping flags that consume their next token, and return
    the first non-flag token after position 0 (the binary name). If no clear
    output is found, return ``len(cmd) - 1``.
    """
    n = len(cmd)
    if n <= 1:
        return n - 1 if n else -1

    # Walk from the end backwards: the last *positional* token is conventionally
    # the output. This is more robust than trying to model every ffmpeg flag.
    for i in range(n - 1, 0, -1):
        token = cmd[i]
        if token.startswith("-"):
            continue
        # Token is positional. Make sure prior token is not a value-taking flag
        # whose value we just touched.
        if i - 1 >= 0 and cmd[i - 1] in _FLAGS_WITH_ARG:
            continue
        return i

    return n - 1


def _faststart_already_present(cmd: Sequence[str]) -> bool:
    """Return True if the command already requests +faststart."""
    for i, token in enumerate(cmd):
        if token == "-movflags" and i + 1 < len(cmd):
            value = cmd[i + 1]
            # Accept "+faststart", "faststart", or combined like "+faststart+rtphint".
            if "faststart" in value:
                return True
        # Some forms write "-movflags=+faststart" as one token.
        if token.startswith("-movflags=") and "faststart" in token:
            return True
    return False


def extend_ffmpeg_cmd(cmd: List[str]) -> List[str]:
    """Return a new ffmpeg command list with ``-movflags +faststart`` injected.

    The flag is spliced immediately before the detected output path. If the
    flag is already present (in any equivalent form) the input is returned
    unchanged. The original ``cmd`` is not mutated.

    Args:
        cmd: An ffmpeg command as a list of string tokens (as accepted by
            :class:`subprocess.Popen` with ``shell=False``).

    Returns:
        A new list. Identical to ``cmd`` if faststart is already present or
        if no MP4-family output path can be detected.
    """
    if not isinstance(cmd, list):
        raise TypeError(f"cmd must be list, got {type(cmd).__name__}")

    if _faststart_already_present(cmd):
        return list(cmd)

    out_idx = _find_output_index(cmd)
    if out_idx <= 0:
        # No reasonable output position; return unchanged.
        return list(cmd)

    # Only inject for MP4-family containers; other containers (mkv, webm, etc.)
    # don't use the moov atom and ffmpeg would warn or error.
    if not _is_output_path(cmd[out_idx]):
        return list(cmd)

    new_cmd = list(cmd)
    new_cmd.insert(out_idx, "-movflags")
    new_cmd.insert(out_idx + 1, "+faststart")
    return new_cmd


def _selftest() -> int:
    """Run a few sanity checks. Returns 0 on success, 1 on failure."""
    cases = [
        (
            ["ffmpeg", "-y", "-i", "in.mkv", "out.mp4"],
            ["ffmpeg", "-y", "-i", "in.mkv", "-movflags", "+faststart", "out.mp4"],
        ),
        (
            ["ffmpeg", "-i", "in.mkv", "-c:v", "libx264", "out.mov"],
            ["ffmpeg", "-i", "in.mkv", "-c:v", "libx264", "-movflags", "+faststart", "out.mov"],
        ),
        (
            # Idempotent.
            ["ffmpeg", "-i", "x.mkv", "-movflags", "+faststart", "y.mp4"],
            ["ffmpeg", "-i", "x.mkv", "-movflags", "+faststart", "y.mp4"],
        ),
        (
            # Non-mp4 output: untouched.
            ["ffmpeg", "-i", "x.mp4", "out.mkv"],
            ["ffmpeg", "-i", "x.mp4", "out.mkv"],
        ),
        (
            # Output already has combined movflags.
            ["ffmpeg", "-i", "x.mkv", "-movflags", "+faststart+rtphint", "out.mp4"],
            ["ffmpeg", "-i", "x.mkv", "-movflags", "+faststart+rtphint", "out.mp4"],
        ),
    ]
    failed = 0
    for inp, expected in cases:
        got = extend_ffmpeg_cmd(inp)
        if got != expected:
            print(f"FAIL: {inp}\n  got     {got}\n  expected{expected}", file=sys.stderr)
            failed += 1
    if failed == 0:
        print("mp4_faststart selftest: OK")
        return 0
    print(f"mp4_faststart selftest: {failed} failure(s)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(_selftest())
