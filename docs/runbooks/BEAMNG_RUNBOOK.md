# BeamNG Operator Runbook

This runbook provides step-by-step instructions for capturing vehicle telemetry from BeamNG.drive and converting it to buyer-spec format.

---

## 1. Prerequisites

Before proceeding, ensure the following requirements are met:

| Requirement | Details |
|-------------|---------|
| **Host OS** | Windows 10 or Windows 11 |
| **BeamNG.drive** | Steam installation ($24.99) - [Steam Store Link](https://store.steampowered.com/app/284160/BeamNGdrive/) |
| **BeamNGpy** | Version 1.27 or higher, installed via pip |
| **Python** | Python 3.8+ recommended |
| **jq** | For JSON parsing verification |

### Install BeamNGpy

```bash
pip install beamngpy>=1.27
```

### CI / Cluster Dry-Run

BeamNG has a pure-Python mock mode for smoke checks on macOS, Linux, and
headless cluster workers. It does not require BeamNG.drive, BeamNGpy, Steam, or
research mode, but it emits the same observation envelope as the real adapter:
`timestamp`, `ego_pose`, `camera`, `vehicle_sensors`, and `source`.

```bash
PYTHONPATH=src python -m oyster_agent_runner.environments.beamng_drive \
  --mock \
  --duration 1 \
  --frequency 10 \
  --output /tmp/beamng_mock_observations.json
```

Verify the dry-run output is JSON and follows the plug-and-play contract:

```bash
jq '.[0] | keys' /tmp/beamng_mock_observations.json
jq '.[0].source' /tmp/beamng_mock_observations.json
```

Use the real BeamNGpy path only on a Windows host with BeamNG.drive installed
and research mode enabled. If the SDK is missing, the adapter raises a clear
`RuntimeError` telling the operator to install `beamngpy` or run `--mock`.

---

## 2. Enable Research Mode

Research mode is required for BeamNGpy to communicate with the game.

### Step 2.1: Edit techlauncher.lua

1. Navigate to your BeamNG.drive installation directory:
   ```
   C:\Program Files (x86)\Steam\steamapps\common\BeamNG.drive\
   ```

2. Open `lua/common/techlauncher.lua` in a text editor.

3. Add the following line near the top of the file:
   ```lua
   researchMode = true
   ```

4. Save the file.

### Step 2.2: Restart the Game

Close and relaunch BeamNG.drive completely.

### Step 2.3: Verify Research Mode Active

Launch the game and check the console output or log files. You should see:
```
[INFO] Research mode active
```

Alternatively, check the in-game console (press `~` or `F11`) for the research mode confirmation message.

---

## 3. Start Local Capture

Use the telemetry capture script to record vehicle data.

### Full CLI with All 7 Flags

```bash
/Users/howardlee/Downloads/oyster-agent-runner/bin/beamng_telemetry_capture.py \
    --host 127.0.0.1 \
    --port 64256 \
    --vehicle-id "player" \
    --output /Users/howardlee/Downloads/oyster-agent-runner/output/engine_telemetry.json \
    --duration 60 \
    --sample-rate 60 \
    --fields "pos,rot,vel,ang_vel,throttle,brake,steering,rpm,gear"
```

### Flag Descriptions

| Flag | Description | Default |
|------|-------------|---------|
| `--host` | BeamNG.research server host | `127.0.0.1` |
| `--port` | BeamNG.research server port | `64256` |
| `--vehicle-id` | Target vehicle identifier | `player` |
| `--output` | Output JSON file path | Required |
| `--duration` | Capture duration in seconds | `60` |
| `--sample-rate` | Samples per second | `60` |
| `--fields` | Comma-separated telemetry fields to capture | All fields |

---

## 4. Verify Output

After capture completes, verify the output file contains valid telemetry data.

### Check First Frame

```bash
cat /Users/howardlee/Downloads/oyster-agent-runner/output/engine_telemetry.json | jq ".frames[0]"
```

### Expected Fields

The output should contain the following fields in each frame:

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | float | Simulation time in seconds |
| `pos` | [x, y, z] | World position (BeamNG coordinates) |
| `rot` | [x, y, z, w] | Quaternion rotation |
| `vel` | [x, y, z] | Linear velocity (m/s) |
| `ang_vel` | [x, y, z] | Angular velocity (rad/s) |
| `throttle` | float | Throttle input (0.0 - 1.0) |
| `brake` | float | Brake input (0.0 - 1.0) |
| `steering` | float | Steering input (-1.0 to 1.0) |
| `rpm` | float | Engine RPM |
| `gear` | int | Current gear (-1 = reverse, 0 = neutral) |

### Sample Output

```json
{
  "timestamp": 0.016667,
  "pos": [123.45, -45.67, 12.34],
  "rot": [0.0, 0.0, 0.0, 1.0],
  "vel": [5.2, 0.1, 0.0],
  "ang_vel": [0.0, 0.0, 0.01],
  "throttle": 0.5,
  "brake": 0.0,
  "steering": 0.0,
  "rpm": 2500.0,
  "gear": 2
}
```

---

## 5. Convert to Buyer-Spec

Convert the raw engine telemetry to buyer-spec format.

### Conversion Command

```bash
python /Users/howardlee/Downloads/oyster-agent-runner/bin/convert_to_buyer_spec.py \
    --engine-fields-from /Users/howardlee/Downloads/oyster-agent-runner/output/engine_telemetry.json \
    --output /Users/howardlee/Downloads/oyster-agent-runner/output/buyer_spec.json \
    --normalize-coordinates \
    --validate-schema
```

### Additional Flags

| Flag | Description |
|------|-------------|
| `--engine-fields-from` | Source engine telemetry JSON file |
| `--output` | Destination buyer-spec JSON file |
| `--normalize-coordinates` | Apply coordinate transformation (Z-up to Y-up) |
| `--validate-schema` | Validate output against buyer-spec schema |

---

## 6. Lint + Pack

Validate and package the buyer-spec output.

### Lint Buyer Spec

```bash
python /Users/howardlee/Downloads/oyster-agent-runner/bin/lint_buyer_spec.py \
    --input /Users/howardlee/Downloads/oyster-agent-runner/output/buyer_spec.json \
    --strict
```

### Pack for Delivery

```bash
/Users/howardlee/Downloads/oyster-agent-runner/bin/buyer_spec_demo_pack.sh \
    /Users/howardlee/Downloads/oyster-agent-runner/output/buyer_spec.json \
    /Users/howardlee/Downloads/oyster-agent-runner/output/packed/
```

This creates a distributable archive with all required metadata files.

---

## 7. Troubleshooting

| Error | Symptoms | Fix |
|-------|----------|-----|
| **UDP Port Blocked** | Connection refused, timeout errors | 1. Check Windows Firewall settings<br>2. Allow BeamNG.drive through firewall<br>3. Verify port 64256 is not in use: `netstat -an \| findstr 64256` |
| **BeamNGpy Import Fail** | `ModuleNotFoundError: No module named 'beamngpy'` | 1. Verify Python environment is active<br>2. Reinstall: `pip install --upgrade beamngpy`<br>3. Check pip list: `pip show beamngpy` |
| **Vehicle ID Wrong** | `Vehicle not found` error | 1. List available vehicles in-game<br>2. Use correct ID (default: `player`)<br>3. Check vehicle spawn name in scenario |
| **Research Mode Not Active** | Connection fails, no telemetry | 1. Re-edit `techlauncher.lua`<br>2. Ensure `researchMode = true` is set<br>3. Fully restart the game |
| **Invalid JSON Output** | Parse errors, missing fields | 1. Check disk space<br>2. Verify write permissions<br>3. Reduce `--sample-rate` if too fast |
| **Coordinate Mismatch** | Vehicle position appears wrong | 1. Ensure `--normalize-coordinates` flag is used<br>2. See Section 8 for coordinate conventions |

---

## 8. Coordinate Convention Notes

BeamNG.drive and buyer-spec use different coordinate systems. Understanding this transformation is critical for correct data interpretation.

### BeamNG Coordinate System (Z-Up)

```
      Z (up)
      |
      |
      +------ Y (forward)
     /
    X (right)
```

- **X**: Right
- **Y**: Forward
- **Z**: Up

### Buyer-Spec Coordinate System (Y-Up)

```
      Y (up)
      |
      |
      +------ Z (forward)
     /
    X (right)
```

- **X**: Right
- **Z**: Forward
- **Y**: Up

### Transformation Matrix

To convert from BeamNG to buyer-spec coordinates:

| BeamNG | Buyer-Spec |
|--------|------------|
| X | X |
| Y | Z |
| Z | Y |

### Example Transformation

**BeamNG position**: `[100.0, 200.0, 5.0]`

**Buyer-spec position**: `[100.0, 5.0, 200.0]`

### Rotation Conversion

Quaternions must also be transformed. The `--normalize-coordinates` flag in `convert_to_buyer_spec.py` handles this automatically:

```python
# Pseudocode for quaternion transformation
# q_buyer = q_beamng * rotation_matrix
# Where rotation_matrix swaps Y and Z axes
```

> **Note**: Always use the `--normalize-coordinates` flag to ensure correct transformation. Manual conversion is error-prone.

---

## Quick Reference

```bash
# Full pipeline
pip install beamngpy>=1.27

# Enable research mode in techlauncher.lua, then restart game

/Users/howardlee/Downloads/oyster-agent-runner/bin/beamng_telemetry_capture.py \
    --host 127.0.0.1 --port 64256 --vehicle-id "player" \
    --output /Users/howardlee/Downloads/oyster-agent-runner/output/engine_telemetry.json \
    --duration 60 --sample-rate 60 \
    --fields "pos,rot,vel,ang_vel,throttle,brake,steering,rpm,gear"

cat /Users/howardlee/Downloads/oyster-agent-runner/output/engine_telemetry.json | jq ".frames[0]"

python /Users/howardlee/Downloads/oyster-agent-runner/bin/convert_to_buyer_spec.py \
    --engine-fields-from /Users/howardlee/Downloads/oyster-agent-runner/output/engine_telemetry.json \
    --output /Users/howardlee/Downloads/oyster-agent-runner/output/buyer_spec.json \
    --normalize-coordinates --validate-schema

python /Users/howardlee/Downloads/oyster-agent-runner/bin/lint_buyer_spec.py \
    --input /Users/howardlee/Downloads/oyster-agent-runner/output/buyer_spec.json --strict

/Users/howardlee/Downloads/oyster-agent-runner/bin/buyer_spec_demo_pack.sh \
    /Users/howardlee/Downloads/oyster-agent-runner/output/buyer_spec.json \
    /Users/howardlee/Downloads/oyster-agent-runner/output/packed/
```

---

*Last updated: 2024*
