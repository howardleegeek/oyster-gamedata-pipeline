#!/usr/bin/env python3
"""R013 · mineflayer_runner.py — Python wrapper to launch mineflayer + ScriptedProvider."""

from __future__ import annotations

import argparse
import shutil
import signal
import subprocess
import sys
from pathlib import Path

VALID_MODES = {"normal", "wasd_balanced", "special", "loop"}


def find_node_executable() -> str:
    """Locate node 18+ binary. Raise RuntimeError if missing."""
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node executable not found in PATH")
    try:
        result = subprocess.run(
            [node, "--version"],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("node --version timed out") from None
    version_str = result.stdout.strip().lstrip("v")
    try:
        major = int(version_str.split(".")[0])
    except (ValueError, IndexError):
        raise RuntimeError(f"Cannot parse node version: {version_str!r}") from None
    if major < 18:
        raise RuntimeError(f"node >= 18 required, found {version_str}")
    return node


def find_bot_script() -> str:
    """Locate vendor/recorder/bot.js (submodule) or fallback bots/bot.js."""
    candidates = [
        Path(__file__).resolve().parent.parent / "vendor" / "recorder" / "bot.js",
        Path(__file__).resolve().parent.parent / "bots" / "bot.js",
    ]
    for p in candidates:
        if p.is_file():
            return str(p)
    raise FileNotFoundError(
        f"bot.js not found. Tried: {[str(p) for p in candidates]}"
    )


def build_node_args(
    bot_script: str,
    server_host: str,
    server_port: int,
    username: str,
    mode: str,
    duration_sec: float,
    output_dir: str,
    seed: int = 42,
) -> list[str]:
    """Build ['node', bot_script, '--host', ..., '--mode', mode, ...] arg list."""
    if mode not in VALID_MODES:
        raise ValueError(f"Invalid mode {mode!r}. Must be one of {sorted(VALID_MODES)}")
    node = find_node_executable()
    return [
        node, bot_script,
        "--host", server_host,
        "--port", str(server_port),
        "--username", username,
        "--mode", mode,
        "--duration", str(duration_sec),
        "--output", output_dir,
        "--seed", str(seed),
    ]


def run_mineflayer(
    server_host: str = "localhost",
    server_port: int = 25565,
    username: str = "DataPilot",
    mode: str = "wasd_balanced",
    duration_sec: float = 360.0,
    output_dir: str = "out/mineflayer",
    seed: int = 42,
    verbose: bool = False,
) -> int:
    """subprocess.run node bot.js with timeout=duration_sec+30.
    Pipe stdout to log file. Return exit code."""
    if mode not in VALID_MODES:
        raise ValueError(f"Invalid mode {mode!r}. Must be one of {sorted(VALID_MODES)}")

    out = Path(output_dir)
    if out.exists():
        raise FileExistsError(f"output_dir already exists: {output_dir}")
    out.mkdir(parents=True, exist_ok=True)

    log_path = out / "mineflayer.log"
    bot_script = find_bot_script()
    args = build_node_args(
        bot_script, server_host, server_port, username,
        mode, duration_sec, output_dir, seed,
    )

    timeout = duration_sec + 30
    if verbose:
        print(f"[mineflayer_runner] {' '.join(args)}")
        print(f"[mineflayer_runner] timeout={timeout}s  log={log_path}")

    with open(log_path, "w", encoding="utf-8") as log_f:
        proc = subprocess.Popen(
            args,
            stdout=log_f,
            stderr=subprocess.STDOUT,
        )
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # SIGTERM → wait 5s → SIGKILL
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            return -signal.SIGTERM.value
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    """CLI: --server / --port / --username / --mode / --duration / --output / --seed"""
    parser = argparse.ArgumentParser(
        description="Launch a mineflayer bot with ScriptedProvider.",
    )
    parser.add_argument("--server", default="localhost", help="Minecraft server host")
    parser.add_argument("--port", type=int, default=25565, help="Minecraft server port")
    parser.add_argument("--username", default="DataPilot", help="Bot username")
    parser.add_argument(
        "--mode", default="wasd_balanced",
        choices=sorted(VALID_MODES),
        help="Bot behaviour mode",
    )
    parser.add_argument("--duration", type=float, default=360.0, help="Max run time (seconds)")
    parser.add_argument("--output", default="out/mineflayer", help="Output directory")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--verbose", action="store_true", help="Print command before running")

    args = parser.parse_args(argv)

    try:
        return run_mineflayer(
            server_host=args.server,
            server_port=args.port,
            username=args.username,
            mode=args.mode,
            duration_sec=args.duration,
            output_dir=args.output,
            seed=args.seed,
            verbose=args.verbose,
        )
    except (RuntimeError, FileNotFoundError, FileExistsError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
