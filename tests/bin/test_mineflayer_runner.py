"""Tests for bin/mineflayer_runner.py."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure bin/ is on the import path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "bin"))

import mineflayer_runner as mr


# ── 1. test_find_node_returns_path ──────────────────────────────────────────
def test_find_node_returns_path():
    """Mock subprocess so find_node_executable returns a path."""
    with (
        patch("mineflayer_runner.shutil.which", return_value="/usr/bin/node"),
        patch("mineflayer_runner.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(stdout="v20.11.0", stderr="")
        result = mr.find_node_executable()
        assert result == "/usr/bin/node"
        mock_run.assert_called_once_with(
            ["/usr/bin/node", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )


# ── 2. test_find_node_raises_when_missing ───────────────────────────────────
def test_find_node_raises_when_missing():
    """When node is not in PATH, raise RuntimeError."""
    with (
        patch("mineflayer_runner.shutil.which", return_value=None),
        pytest.raises(RuntimeError, match="node executable not found"),
    ):
        mr.find_node_executable()


def test_find_node_raises_when_old_version():
    """When node version < 18, raise RuntimeError."""
    with (
        patch("mineflayer_runner.shutil.which", return_value="/usr/bin/node"),
        patch("mineflayer_runner.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(stdout="v16.20.0", stderr="")
        with pytest.raises(RuntimeError, match="node >= 18 required"):
            mr.find_node_executable()


# ── 3. test_build_args_includes_all_flags ───────────────────────────────────
def test_build_args_includes_all_flags():
    """build_node_args must include every expected flag."""
    with patch("mineflayer_runner.find_node_executable", return_value="/usr/bin/node"):
        args = mr.build_node_args(
            bot_script="/path/bot.js",
            server_host="mc.example.com",
            server_port=25566,
            username="TestBot",
            mode="normal",
            duration_sec=120.0,
            output_dir="/tmp/out",
            seed=99,
        )
    assert args[0] == "/usr/bin/node"
    assert args[1] == "/path/bot.js"
    assert "--host" in args
    assert "mc.example.com" in args
    assert "--port" in args
    assert "25566" in args
    assert "--username" in args
    assert "TestBot" in args
    assert "--mode" in args
    assert "normal" in args
    assert "--duration" in args
    assert "120.0" in args
    assert "--output" in args
    assert "/tmp/out" in args
    assert "--seed" in args
    assert "99" in args


# ── 4. test_mode_validation ────────────────────────────────────────────────
def test_mode_validation():
    """build_node_args rejects unknown mode."""
    with (
        patch("mineflayer_runner.find_node_executable", return_value="/usr/bin/node"),
        pytest.raises(ValueError, match="Invalid mode"),
    ):
        mr.build_node_args(
                bot_script="x",
                server_host="h",
                server_port=1,
                username="u",
                mode="INVALID",
                duration_sec=1,
                output_dir="o",
            )


def test_run_mineflayer_mode_validation():
    """run_mineflayer also rejects unknown mode."""
    with pytest.raises(ValueError, match="Invalid mode"):
        mr.run_mineflayer(mode="bogus")


# ── 5. test_run_mineflayer_uses_subprocess_run ─────────────────────────────
def test_run_mineflayer_uses_subprocess_run(tmp_path):
    """run_mineflayer should call subprocess.Popen with correct args."""
    out_dir = str(tmp_path / "out")
    with (
        patch("mineflayer_runner.find_node_executable", return_value="/usr/bin/node"),
        patch("mineflayer_runner.find_bot_script", return_value="/path/bot.js"),
    ):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.wait.return_value = None
        with patch("mineflayer_runner.subprocess.Popen", return_value=mock_proc) as mock_popen:
            rc = mr.run_mineflayer(
                server_host="localhost",
                server_port=25565,
                username="DataPilot",
                mode="wasd_balanced",
                duration_sec=60.0,
                output_dir=out_dir,
                seed=42,
            )
            assert rc == 0
            mock_popen.assert_called_once()
            call_args = mock_popen.call_args[0][0]
            assert call_args[0] == "/usr/bin/node"
            assert call_args[1] == "/path/bot.js"
            assert "--mode" in call_args
            assert "wasd_balanced" in call_args


# ── 6. test_main_argparse ──────────────────────────────────────────────────
def test_main_argparse():
    """main() should parse CLI args and call run_mineflayer."""
    with patch("mineflayer_runner.run_mineflayer", return_value=0) as mock_run:
        rc = mr.main(
            [
                "--server",
                "mc.test.com",
                "--port",
                "25566",
                "--username",
                "CLI_Bot",
                "--mode",
                "special",
                "--duration",
                "90",
                "--output",
                "/tmp/cli_out",
                "--seed",
                "7",
            ]
        )
        assert rc == 0
        mock_run.assert_called_once_with(
            server_host="mc.test.com",
            server_port=25566,
            username="CLI_Bot",
            mode="special",
            duration_sec=90.0,
            output_dir="/tmp/cli_out",
            seed=7,
            verbose=False,
        )


def test_main_argparse_defaults():
    """main() should use defaults when no args given."""
    with patch("mineflayer_runner.run_mineflayer", return_value=0) as mock_run:
        mr.main([])
        mock_run.assert_called_once_with(
            server_host="localhost",
            server_port=25565,
            username="DataPilot",
            mode="wasd_balanced",
            duration_sec=360.0,
            output_dir="out/mineflayer",
            seed=42,
            verbose=False,
        )


def test_main_argparse_rejects_bad_mode():
    """argparse should reject invalid --mode values."""
    with pytest.raises(SystemExit):
        mr.main(["--mode", "invalid_mode_xyz"])
