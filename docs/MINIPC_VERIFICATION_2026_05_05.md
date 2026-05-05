# minipc WSL2 E2E Verification — 2026-05-05

> Howard's directive: 'minipc 那边测试 先跑通minecraft 同时其他集群横向扩展'

## Track A: minipc (Windows 11 + WSL2 Ubuntu 22.04) — PASSED

| Step | Result |
|---|---|
| Java 21 (WSL2 OpenJDK) | ✅ `21.0.10+7-Ubuntu-122.04` |
| Node 20 (WSL2) | ✅ `v20.20.2` |
| Paper 1.20.4 jar download | ✅ 40 MB cached |
| `pip install -e .[exr,xlsx,test]` | ✅ after `apt install python3-venv` |
| Paper boot | ✅ **1.5–3.4 s** (warm/cold) |
| `online-mode=false` | ✅ auto-set after first boot |
| Mineflayer bot connect | ✅ |
| `--provider mock` 5-step trajectory | ✅ termination_reason=max_steps |
| Output files | ✅ 5 files (manifest + cot + metadata + inputs + trajectory) |
| Event count | ✅ **63 events** (12 cot + 7 inputs + 17 metadata + 27 trajectory) |
| Wall-clock | ✅ 1.60 s |

**Two-machine reproducibility**: identical 63-event output to mac-air-4 (Apple Silicon) earlier.

## Track B: cluster horizontal expansion (8 W21 specs queued, dispatching)

P0 game extractors (`src/oyster_agent_runner/environments/`):
- G176 beamng_drive.py — pending
- G177 stardew_valley.py — pending
- G178 cyberpunk_2077.py — pending
- G179 cities_skylines.py — pending
- G180 factorio_full.py — pending

Supporting:
- G181 environments/registry.py — pending
- G182 bin/cross_game_test_harness.py — pending
- G183 docs/runbooks/STARDEW_RUNBOOK.md — pending

Cluster will dispatch + complete in parallel; each spec is a NEW-FILE atomic unit (~200-400 LOC).

## Stack reproducibility

| Host | OS | CPU | Paper boot | Total events | Wall-clock |
|---|---|---|---|---|---|
| mac-air-4 | macOS 26 | Apple M-series | 7 s (cold) | 63 | 1.10 s |
| minipc | Win 11 + WSL2 | AMD Ryzen 7 7840HS | 1.5–3.4 s | 63 | 1.60 s |

Same Paper jar, same Mineflayer version, same mock provider → byte-equivalent trajectory output.

## SOP for 老刘 (incremental over docs/SOP_LAO_LIU.md)

If running inside WSL2 Ubuntu 22.04, after `git clone` add:
```bash
sudo apt-get install -y python3-venv python3-pip
python3 -m venv .venv
.venv/bin/pip install -e .[exr,xlsx,test]
```

Then proceed with `bash bin/smoke_phase1.sh` per the main SOP.
