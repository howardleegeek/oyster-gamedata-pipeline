# Quickstart — Buyer-Spec v1 Production Pipeline

**One-command path**:

```bash
# Boot Paper Minecraft server (1.20.4) on localhost:25565
cd /Users/howardli/Downloads/oyster-agent-runner/bin/.cache
/opt/homebrew/opt/openjdk@21/bin/java -Xms1G -Xmx2G -jar paper-1.20.4.jar nogui &

# Run end-to-end: capture → adapt → lint → pack
/Users/howardli/Downloads/oyster-agent-runner/bin/buyer_spec_pipeline.sh \
  --task /Users/howardli/Downloads/oyster-agent-runner/tasks/MC-tutorial-001.json \
  --output /tmp/buyer_delivery.tar.gz \
  --max-steps 9000 \
  --bot-username op_main
```

Output: 11MB tarball with 5 deliverables + lint_report.json + diagnose_report.json + README.md + SHA256SUMS.

## Three-game roadmap status

| Game | Capture | Adapter | Lint | Status |
|---|---|---|---|---|
| Minecraft (Mineflayer + Paper) | `oyster-agent run-mc` | `oyster-agent adapt-buyer-spec` | exit 0 | ✅ production |
| BeamNG.drive | `bin/beamng_telemetry_capture.py` | `bin/convert_to_buyer_spec.py --engine-fields-from` | (pending) | 🟡 needs Windows host |
| CS2 | `bin/cs2_demo_to_engine_telemetry.py` | `bin/convert_to_buyer_spec.py --engine-fields-from` | (pending) | 🟡 needs `.dem` file |

## Prerequisites (one-time setup)

```bash
# Java 21 (for Paper server)
brew install openjdk@21

# Node.js + Mineflayer (for the bot)
cd /Users/howardli/Downloads/oyster-agent-runner/mineflayer
npm install

# Python venvs (already set up — both repos)
# /Users/howardli/Downloads/oyster-agent-runner/.venv
# /Users/howardli/Downloads/oyster-enrichment/.venv

# Placeholder assets (auto-staged on first pipeline run)
# /tmp/oyster_placeholders/{video.mp4, gameinfo.xlsx, depth/*.exr}
```

## Running 100 iterations (validation sprint)

```bash
/Users/howardli/Downloads/oyster-agent-runner/bin/iterate_buyer_spec.sh 100 50
# Logs per-iter to /tmp/oyster_iter_log/iter_NNNN.json
# Summary at /tmp/oyster_iter_log/summary.json
```

## CS2 game-3 path (when Howard provides .dem)

```bash
# 1. Parse demo → engine_telemetry sidecar
python /Users/howardli/Downloads/oyster-enrichment/bin/cs2_demo_to_engine_telemetry.py \
  --demo /path/to/match.dem \
  --output /tmp/cs2_engine_telemetry.json \
  --frame-rate 30 --max-frames 9000

# 2. Adapt original CS2 capture bundle to buyer-spec, merging engine fields
python /Users/howardli/Downloads/oyster-enrichment/bin/convert_to_buyer_spec.py \
  --input-bundle /path/to/cs2-recording/ \
  --output-bundle /tmp/cs2_buyer/ \
  --engine-fields-from /tmp/cs2_engine_telemetry.json

# 3. Lint
python /Users/howardli/Downloads/oyster-enrichment/bin/lint_buyer_spec.py /tmp/cs2_buyer/

# 4. Pack
bash /Users/howardli/Downloads/oyster-enrichment/bin/buyer_spec_demo_pack.sh \
  --bundle /tmp/cs2_buyer/ \
  --output /tmp/cs2_delivery.tar.gz
```

## BeamNG game-2 path (when Howard's Windows host is available)

```bash
# Side A (Windows host running BeamNG.drive in research mode):
python /Users/howardli/Downloads/oyster-enrichment/bin/beamng_telemetry_capture.py \
  --output /tmp/beamng_engine_telemetry.json \
  --host 127.0.0.1 --port 25252 \
  --frame-rate 30 --duration 300

# Side B: same convert/lint/pack as CS2 above.
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `bot died before spawn: kicked: ... received string length is longer than maximum allowed (18 > 16)` | Username >16 chars | Pass `--bot-username` ≤16 chars |
| `VIDEO_TOO_SHORT` at lint | Capture <300s of action_camera records | Add `--pad-to-min-records 9000` to adapt |
| `DEPTH_DIR_MISSING` | Missing depth/ placeholder | Re-run pipeline; `_ensure_placeholders` auto-stages |
| `OpenEXR import failed` | Missing OpenEXR | `pip install OpenEXR Imath` (macOS may need `brew install openexr`) |
| Server won't start | Port 25565 in use | `lsof -i:25565` then kill the conflicting process |
| `eula must be accepted` | Paper server needs EULA | `echo "eula=true" > eula.txt` in server dir |

## Reference

- Production gaps: `docs/PRODUCTION_LINE.md`
- Buyer spec: `oyster-enrichment/docs/BUYER_SPEC_v1_PLAN.md`
- Lint codes: `oyster-enrichment/bin/lint_buyer_spec.py`
- Adapter source: `src/oyster_agent_runner/buyer_spec_adapter.py`
- Pipeline script: `bin/buyer_spec_pipeline.sh`
