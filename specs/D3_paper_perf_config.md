# D3 — Paper server perf config patch (atomic, no recorder dependency)

## Goal
Add a single shell script: `bin/paper_server_start.sh` — boots Paper with
tuned config that **prevents bot keepalive timeouts** (the bug we hit:
"op_full was kicked due to keepalive timeout!" after 45s).

The script:
1. Writes optimized `server.properties` (low view distance, no monsters,
   creative mode, offline-mode for bot connect).
2. Writes optimized `paper-global.yml` and `paper-world-defaults.yml` with
   anti-stutter settings.
3. Boots Paper with `-Xmx4G -Xms2G -XX:+UseG1GC` and Aikar's flags.
4. Waits until port 25565 is LISTEN before returning.
5. Tail's the boot log into stdout for transparency.

## Public API (CLI)

```bash
bin/paper_server_start.sh \
  --paper-jar /path/to/paper.jar \
  --server-dir /path/to/server-workdir \
  [--xmx 4G] [--port 25565] [--view-distance 4]
```

Exits:
- 0 if server reaches LISTEN within 60s
- 1 if any required arg missing
- 2 if jar/dir not found
- 3 if server fails to start within 60s

## Hard requirements

1. Aikar's flags for Paper 1.20.4:
   ```
   -XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=200
   -XX:+UnlockExperimentalVMOptions -XX:+DisableExplicitGC
   -XX:+AlwaysPreTouch -XX:G1NewSizePercent=30 -XX:G1MaxNewSizePercent=40
   -XX:G1HeapRegionSize=8M -XX:G1ReservePercent=20 -XX:G1HeapWastePercent=5
   -XX:G1MixedGCCountTarget=4 -XX:InitiatingHeapOccupancyPercent=15
   -XX:G1MixedGCLiveThresholdPercent=90 -XX:G1RSetUpdatingPauseTimePercent=5
   -XX:SurvivorRatio=32 -XX:+PerfDisableSharedMem -XX:MaxTenuringThreshold=1
   ```
2. server.properties: view-distance=4, simulation-distance=4, spawn-monsters=false,
   spawn-animals=false, generate-structures=false, online-mode=false.
3. eula.txt = `eula=true` (ensure written before Java start).
4. Health-check loop: poll port 25565 every 1s, up to 60s.

## Tests (must pass `pytest -q`)

```python
# tests/test_paper_server_start.py
import os
import subprocess
import time
from pathlib import Path
import pytest


@pytest.mark.skipif(
    not Path("/usr/bin/java").exists() and not Path("/opt/homebrew/opt/openjdk@21/bin/java").exists(),
    reason="Java 21 not installed in test env",
)
def test_script_exists_and_help():
    script = Path("bin/paper_server_start.sh")
    assert script.exists()
    assert script.stat().st_mode & 0o111  # executable
    res = subprocess.run([str(script), "--help"], capture_output=True, text=True)
    # accept rc 0 or 64 (no required args), but should print usage
    assert "paper" in res.stdout.lower() + res.stderr.lower()


def test_missing_args_exits_nonzero():
    res = subprocess.run(["bash", "bin/paper_server_start.sh"], capture_output=True, text=True)
    assert res.returncode != 0


def test_missing_jar_exits_nonzero(tmp_path):
    res = subprocess.run(
        ["bash", "bin/paper_server_start.sh",
         "--paper-jar", str(tmp_path / "nope.jar"),
         "--server-dir", str(tmp_path / "srv")],
        capture_output=True, text=True,
    )
    assert res.returncode == 2
```

## Acceptance

- [ ] `bin/paper_server_start.sh` exists and is executable
- [ ] `pytest tests/test_paper_server_start.py -q` → all 3 tests pass
- [ ] Script runs `--help` cleanly
- [ ] Aikar's flags are literally in the java invocation (grep-able in script)

## Don't

- Don't actually start a Paper server inside the test — too slow / resource
  intensive for CI.
- Don't hardcode any Mac paths.
- Don't modify any other file in the repo.
