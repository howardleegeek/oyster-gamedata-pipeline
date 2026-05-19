# Vendor PC Installation Validator Runbook

<!--
G204 · docs/runbooks/VENDOR_INSTALL_VALIDATOR.md
Purpose: Validator runbook for vendor PCs before recorder.exe deployment.
Checks: GPU model, driver version, Java, Node.js, disk space, network speed.
-->

## Overview

Validates vendor PC prerequisites before running `recorder.exe`.
Complete all checks and document results in the vendor sign-off sheet.

## Pre-Validation Requirements

- **Administrator access** — Required for driver checks and system queries
- **Network connectivity** — Internet access for speed tests
- **Power source** — Laptop must be plugged in for accurate GPU detection

## Section 1: GPU Model and Driver

```powershell
wmic path win32_VideoController get name,AdapterRAM
nvidia-smi --query-gpu=driver_version --format=csv,noheader
```

**Criteria:** NVIDIA RTX 3060+, ≥ 8 GB VRAM, driver ≥ 535.54

## Section 2: Java

```bash
java -version
echo $JAVA_HOME  # or $env:JAVA_HOME on Windows
```

**Criteria:** Java 17+, `JAVA_HOME` set correctly.

## Section 3: Node.js

```bash
node --version
npm --version
```

**Criteria:** Node.js ≥ 18 LTS, npm ≥ 9.

## Section 4: Disk Space

```powershell
Get-PSDrive -PSProvider FileSystem | Select Name,Used,Free  # Windows
df -h  # Linux/macOS
```

**Criteria:** System drive ≥ 50 GB free, recording storage ≥ 500 GB free.

## Section 5: Network Speed

```bash
curl -o /dev/null -w "%{speed_download}\n" -s https://speed.cloudflare.com/__down?bytes=25000000
```

**Criteria:** Download ≥ 50 Mbps, upload ≥ 10 Mbps, latency ≤ 100 ms.

## Automated Validator (Python)

```python
#!/usr/bin/env python3
"""G204 Vendor PC Installation Validator — automated prerequisite checks."""

import argparse
import shutil
import subprocess
import sys
from typing import List, Tuple


def _run(cmd: List[str], timeout: int = 30) -> Tuple[int, str, str]:
    """Execute a command and return (returncode, stdout, stderr)."""
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def check_gpu() -> bool:
    """Validate NVIDIA GPU presence and driver version."""
    rc, out, _ = _run(["nvidia-smi", "--query-gpu=driver_version",
                        "--format=csv,noheader"])
    if rc != 0:
        print("FAIL: nvidia-smi not found or GPU not detected")
        return False
    version = out.splitlines()[0].strip()
    parts = version.split(".")
    major = int(parts[0]) if parts else 0
    minor = int(parts[1]) if len(parts) > 1 else 0
    ok = major > 535 or (major == 535 and minor >= 54)
    print(f"{'PASS' if ok else 'FAIL'}: NVIDIA driver {version} (min 535.54)")
    return ok


def check_java() -> bool:
    """Validate Java 17+ installation."""
    rc, _, err = _run(["java", "-version"])
    version_line = err.splitlines()[0] if err else ""
    if any(v in version_line for v in ("17", "18", "21")):
        print(f"PASS: {version_line.strip()}")
        return True
    print(f"FAIL: Java version not 17+: {version_line.strip()}")
    return False


def check_node() -> bool:
    """Validate Node.js 18+ and npm 9+."""
    rc, out, _ = _run(["node", "--version"])
    if rc != 0:
        print("FAIL: node not found")
        return False
    major = int(out.lstrip("v").split(".")[0])
    ok = major >= 18
    print(f"{'PASS' if ok else 'FAIL'}: Node.js {out} (min v18)")
    rc2, npm_out, _ = _run(["npm", "--version"])
    npm_major = int(npm_out.split(".")[0]) if rc2 == 0 else 0
    npm_ok = npm_major >= 9
    print(f"{'PASS' if npm_ok else 'FAIL'}: npm {npm_out} (min 9)")
    return ok and npm_ok


def check_disk(min_gb: int = 50) -> bool:
    """Validate free disk space on the system drive."""
    usage = shutil.disk_usage("/")
    free_gb = usage.free // (1024 ** 3)
    ok = free_gb >= min_gb
    print(f"{'PASS' if ok else 'FAIL'}: Disk free {free_gb}G (min {min_gb}G)")
    return ok


def check_network() -> bool:
    """Validate network connectivity (basic ping test)."""
    rc, _, _ = _run(["ping", "-c", "1", "-W", "5", "8.8.8.8"], timeout=10)
    ok = rc == 0
    print(f"{'PASS' if ok else 'FAIL'}: Network connectivity to 8.8.8.8")
    return ok


def main(argv: List[str] | None = None) -> int:
    """Run all vendor PC validation checks and return exit code."""
    parser = argparse.ArgumentParser(
        description="Validate vendor PC prerequisites for recorder.exe"
    )
    parser.add_argument("--min-disk-gb", type=int, default=50,
                        help="Minimum free disk space in GB (default: 50)")
    parser.add_argument("--skip", nargs="*", default=[],
                        choices=["gpu", "java", "node", "disk", "network"],
                        help="Checks to skip")
    args = parser.parse_args(argv)

    checks = {
        "gpu": check_gpu,
        "java": check_java,
        "node": check_node,
        "disk": lambda: check_disk(args.min_disk_gb),
        "network": check_network,
    }

    results: List[bool] = []
    for name, fn in checks.items():
        if name in args.skip:
            print(f"SKIP: {name}")
            continue
        results.append(fn())

    passed = sum(results)
    total = len(results)
    print(f"\nResult: {passed}/{total} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
```

## Automated Validator (Bash)

```bash
#!/usr/bin/env bash
# G204 Vendor PC Installation Validator — bash variant
set -euo pipefail
TMPDIR_WORK="$(mktemp -d)"
trap 'rm -rf "${TMPDIR_WORK}"' EXIT

PASS=0; FAIL=0
check() {
    local label="$1"; shift
    if "$@" >/dev/null 2>&1; then echo "PASS: ${label}"; ((PASS++)) || true
    else echo "FAIL: ${label}"; ((FAIL++)) || true; fi
}

echo "=== G204 Vendor PC Validator ==="
if command -v nvidia-smi >/dev/null 2>&1; then
    DRIVER_VER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1 | tr -d '[:space:]')
    echo "INFO: NVIDIA driver ${DRIVER_VER}"
    check "nvidia-smi accessible" nvidia-smi --query-gpu=name --format=csv,noheader
else echo "FAIL: nvidia-smi not found"; ((FAIL++)) || true; fi

if command -v node >/dev/null 2>&1; then
    NODE_VER=$(node --version | tr -d 'v'); NODE_MAJOR=${NODE_VER%%.*}
    if [[ ${NODE_MAJOR} -ge 18 ]]; then echo "PASS: Node.js ${NODE_VER}"; ((PASS++)) || true
    else echo "FAIL: Node.js ${NODE_VER} < 18"; ((FAIL++)) || true; fi
else echo "FAIL: node not found"; ((FAIL++)) || true; fi

DISK_FREE_GB=$(df -BG / | awk 'NR==2{gsub(/G/,"",$4); print $4}')
if [[ ${DISK_FREE_GB} -ge 50 ]]; then echo "PASS: Disk free ${DISK_FREE_GB}G"; ((PASS++)) || true
else echo "FAIL: Disk free ${DISK_FREE_GB}G < 50G"; ((FAIL++)) || true; fi

check "Network connectivity" ping -c 1 -W 5 8.8.8.8

TOTAL=$((PASS + FAIL))
echo ""; echo "Result: ${PASS}/${TOTAL} checks passed"
[[ ${FAIL} -eq 0 ]] && exit 0 || exit 1
```

## Sign-Off Checklist

| # | Check | Status | Notes |
|---|-------|--------|-------|
| 1 | GPU model ≥ RTX 3060 | ☐ | |
| 2 | GPU VRAM ≥ 8 GB | ☐ | |
| 3 | NVIDIA driver ≥ 535.54 | ☐ | |
| 4 | Java ≥ 17 | ☐ | |
| 5 | JAVA_HOME set | ☐ | |
| 6 | Node.js ≥ 18 LTS | ☐ | |
| 7 | npm ≥ 9 | ☐ | |
| 8 | System disk ≥ 50 GB free | ☐ | |
| 9 | Storage disk ≥ 500 GB free | ☐ | |
| 10 | Network ≥ 50 Mbps down | ☐ | |

**Validated by:** ________________  **Date:** ________________
