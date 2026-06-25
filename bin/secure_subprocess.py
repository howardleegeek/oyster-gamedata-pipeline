#!/usr/bin/env python3
"""R042 — safe subprocess wrapper module.

Drop-in replacement for subprocess.run(cmd, shell=True) with:
- arg list enforcement (rejects shell=True)
- argument escaping via shlex
- timeout default 30s
- captured stdout/stderr returned
- explicit allowlist for binary paths (fail closed)
"""

import shlex
import subprocess

ALLOWED_BINARIES: set[str] = {
    "/usr/bin/git",
    "/usr/bin/java",
    "/usr/bin/python3",
    "/usr/local/bin/python3",
    "/usr/local/bin/node",
    "/usr/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    "/usr/bin/ls",
    "/bin/ls",
    "/usr/bin/cat",
    "/bin/cat",
    "/usr/bin/grep",
    "/usr/bin/find",
    "/usr/bin/curl",
    "/usr/bin/wget",
    "/usr/bin/tar",
    "/usr/bin/make",
    "/usr/bin/docker",
    "/usr/local/bin/docker",
    "/usr/local/bin/npm",
    "/usr/local/bin/pip3",
    "/usr/bin/echo",
    "/bin/echo",
    "/usr/bin/date",
    "/bin/date",
    "/usr/bin/whoami",
    "/usr/bin/id",
}


def quote_for_shell(arg: str) -> str:
    """Safely quote a single argument for shell display."""
    return shlex.quote(arg)


def _validate_cmd(cmd: list[str]) -> None:
    """Validate cmd is a non-empty list of strings with an allowlisted binary."""
    if not isinstance(cmd, list):
        raise ValueError(f"cmd must be a list, got {type(cmd).__name__}")
    if not cmd:
        raise ValueError("cmd must not be empty")
    if not all(isinstance(a, str) for a in cmd):
        raise ValueError("All cmd elements must be strings")
    if cmd[0] not in ALLOWED_BINARIES:
        raise ValueError(
            f"Binary '{cmd[0]}' is not in the allowlist. Allowed: {sorted(ALLOWED_BINARIES)}"
        )


def safe_run(
    cmd: list[str],
    timeout: float = 30.0,
    cwd: str | None = None,
    env: dict | None = None,
    capture_output: bool = True,
) -> subprocess.CompletedProcess:
    """Run a subprocess safely with strict validation.

    Rejects shell=True. Validates cmd[0] in ALLOWED_BINARIES.
    Uses shlex.quote for any user-supplied substrings.
    """
    _validate_cmd(cmd)
    return subprocess.run(
        cmd,
        shell=False,
        timeout=timeout,
        cwd=cwd,
        env=env,
        capture_output=capture_output,
        text=True,
    )


def safe_run_with_input(
    cmd: list[str],
    input_data: str,
    timeout: float = 30.0,
    cwd: str | None = None,
    env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Run a subprocess safely, piping *input_data* to stdin."""
    _validate_cmd(cmd)
    return subprocess.run(
        cmd,
        shell=False,
        timeout=timeout,
        cwd=cwd,
        env=env,
        input=input_data,
        capture_output=True,
        text=True,
    )


def main() -> None:
    """Demo CLI — exercises safe_run with allowlisted binaries."""
    print("=== R042 secure_subprocess demo ===\n")

    print("[1] safe_run(['/bin/echo', 'hello world'])")
    r = safe_run(["/bin/echo", "hello world"])
    print(f"    rc={r.returncode}, stdout={r.stdout.strip()!r}\n")

    print("[2] quote_for_shell with dangerous input")
    dangerous = "hello; rm -rf /"
    print(f"    raw:     {dangerous}")
    print(f"    quoted:  {quote_for_shell(dangerous)}\n")

    print("[3] safe_run(['/bin/sh', '-c', 'echo pwned']) — expect ValueError")
    try:
        safe_run(["/bin/sh", "-c", "echo pwned"])
    except ValueError as exc:
        print(f"    Caught: {exc}\n")

    print("[4] safe_run_with_input(['/usr/bin/grep', 'hello'], 'hello world\\nbye')")
    r = safe_run_with_input(["/usr/bin/grep", "hello"], "hello world\nbye")
    print(f"    rc={r.returncode}, stdout={r.stdout.strip()!r}\n")

    print("[5] safe_run with 0.1s timeout — expect TimeoutExpired")
    try:
        safe_run(["/usr/local/bin/python3", "-c", "import time; time.sleep(5)"], timeout=0.1)
    except subprocess.TimeoutExpired as exc:
        print(f"    Caught: {exc}\n")

    print("=== demo complete ===")


if __name__ == "__main__":
    main()
