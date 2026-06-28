#!/usr/bin/env python3
"""Tests for bin/recorder_window_capture_helper.py (spec G263)."""

from __future__ import annotations

import json
import os
import sys
from unittest import mock

import pytest

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

from bin.recorder_window_capture_helper import (  # noqa: E402
    WindowRect,
    build_window_args,
    enumerate_windows,
    find_window,
    is_windows,
    main,
)


def test_window_rect_dimensions() -> None:
    r = WindowRect(title="X", hwnd=1, left=10, top=20, right=110, bottom=120)
    assert r.width == 100
    assert r.height == 100


def test_window_rect_zero_area() -> None:
    r = WindowRect(title="X", hwnd=1, left=50, top=50, right=50, bottom=50)
    assert r.width == 0 and r.height == 0


def test_enumerate_windows_non_windows_returns_empty() -> None:
    """On non-Windows hosts (CI / Howard's Mac), returns an empty list."""
    if is_windows():
        pytest.skip("Test runs on non-Windows hosts only")
    assert enumerate_windows() == []


def test_find_window_case_insensitive() -> None:
    fake = [
        WindowRect("Minecraft 1.20.1", 100, 0, 0, 1920, 1080),
        WindowRect("Discord", 200, 0, 0, 800, 600),
    ]
    with mock.patch("bin.recorder_window_capture_helper.enumerate_windows", return_value=fake):
        assert find_window("MINECRAFT").hwnd == 100
        assert find_window("discord").hwnd == 200
        assert find_window("notepad") is None


def test_build_window_args_non_windows_raises() -> None:
    if is_windows():
        pytest.skip("Test runs on non-Windows hosts only")
    with pytest.raises(NotImplementedError):
        build_window_args("Minecraft")


def test_build_window_args_with_match() -> None:
    fake = WindowRect("Minecraft", 1, 100, 200, 1300, 920)
    with (
        mock.patch("bin.recorder_window_capture_helper.is_windows", return_value=True),
        mock.patch("bin.recorder_window_capture_helper.find_window", return_value=fake),
    ):
        args, rect = build_window_args("Minecraft", framerate=60)
    assert rect == fake
    assert "-f" in args and "gdigrab" in args
    assert "-offset_x" in args and "100" in args
    assert "-offset_y" in args and "200" in args
    assert "-video_size" in args and "1200x720" in args
    assert "-framerate" in args and "60" in args
    assert args[-2:] == ["-i", "desktop"]


def test_build_window_args_no_match_raises() -> None:
    with (
        mock.patch("bin.recorder_window_capture_helper.is_windows", return_value=True),
        mock.patch("bin.recorder_window_capture_helper.find_window", return_value=None),
        pytest.raises(LookupError),
    ):
        build_window_args("nope")


def test_build_window_args_zero_area_raises() -> None:
    bad = WindowRect("Minimised", 1, 0, 0, 0, 0)
    with (
        mock.patch("bin.recorder_window_capture_helper.is_windows", return_value=True),
        mock.patch("bin.recorder_window_capture_helper.find_window", return_value=bad),
        pytest.raises(ValueError),
    ):
        build_window_args("Minimised")


def test_main_non_windows_reports_unsupported(capsys: pytest.CaptureFixture[str]) -> None:
    """On non-Windows the CLI prints a JSON payload and returns 3."""
    if is_windows():
        pytest.skip("Test runs on non-Windows hosts only")
    rc = main(["--title", "anything"])
    assert rc == 3
    captured = capsys.readouterr().out
    payload = json.loads(captured)
    assert payload["platform_supported"] is False
