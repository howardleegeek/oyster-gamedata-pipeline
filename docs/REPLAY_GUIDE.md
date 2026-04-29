# Phase 1 Replay Guide

Every Phase 1 trajectory bundle ships with the data you need to **walk
the agent's decisions step-by-step**, **verify the bundle is internally
consistent**, and **re-execute the recorded actions against a fresh
environment** to detect drift. This guide tells a buyer how.

## What you receive

```
trajectory_<id>/
├── manifest.json     ← session metadata + alignment proof + counts
├── cot.jsonl         ← LLM thinking + reasoning + actions
├── metadata.jsonl    ← per-step world-state snapshots
└── inputs.jsonl      ← bot actions only
```

(Phase 2 adds `video.mp4` + `frames.jsonl`. The replay tool ignores them
when present and works on any Phase 1 bundle.)

## Quickstart

The package ships a `replay` subcommand on the `oyster-agent` CLI. From
the bundle's parent directory:

```bash
# 1. List the steps the agent took.
python -m oyster_agent_runner.cli replay --manifest trajectory_<id>/manifest.json

# 2. Verify the bundle is structurally consistent.
python -m oyster_agent_runner.cli replay --manifest trajectory_<id>/manifest.json --check

# 3. Re-execute against a fresh MockEnvironment and detect drift.
python -m oyster_agent_runner.cli replay --manifest trajectory_<id>/manifest.json --re-execute
```

Exit code is non-zero on a failed `--check` or non-empty drift report so
you can wire this into CI.

## Programmatic API

```python
from pathlib import Path
from oyster_agent_runner.replay import Replayer

replayer = Replayer(Path("trajectory_demo-001/manifest.json"))

# 1. Walk the steps.
for step in replayer.iter_steps():
    print(
        f"step {step.step_idx}: action={step.action} "
        f"reward={step.reward} timing={step.timing_ms:.1f} ms"
    )

# 2. Verify consistency. Report has `.ok`, `.issues`, and per-stream counts.
report = replayer.verify_consistency()
assert report.ok, report.issues

# 3. Re-execute. By default replays against a fresh MockEnvironment;
#    pass `env=...` to bring your own.
drift = replayer.replay_against()
assert drift.ok, drift.divergence_reasons
```

## What `iter_steps` returns

Each `ReplayStep` is a frozen dataclass with these fields:

| Field          | Type             | Meaning                                              |
| -------------- | ---------------- | ---------------------------------------------------- |
| `step_idx`     | `int`            | Zero-based step index from the AGENT_STEP event      |
| `observation`  | `Any`            | Pre-step world state (from metadata.jsonl)           |
| `thinking`     | `str` \| `None`  | LLM extended-thinking text (when captured)           |
| `reasoning`    | `str` \| `None`  | LLM externalized reasoning for this step             |
| `action`       | `dict` \| `None` | The action dispatched to the environment             |
| `reward`       | `float` \| `None`| Scalar reward returned by the env                    |
| `success_flag` | `bool`           | True iff the env signalled `done=True` at this step  |
| `timing_ms`    | `float`          | Wall-clock gap since the previous step (ms)          |
| `timestamp_sec`| `float`          | Monotonic seconds since session start                |

## What `verify_consistency` checks

- Each stream's timestamps are non-decreasing.
- AGENT_STEP indices in `metadata.jsonl` are contiguous from `0`.
- Every `ACTION` in `cot.jsonl` has a co-timestamped `ACTION` in `inputs.jsonl`.
- Every step has at least one `OBSERVATION` and one `ACTION`.
- No event lies outside `[START_ts, END_ts]` of its own stream.
- `manifest.alignment.{cot,metadata,input}_event_count` equal the actual
  line counts.
- `manifest.alignment.max_timestamp_sec` equals the observed maximum.
- `manifest.result.total_steps` equals the AGENT_STEP count.
- `manifest.result.success` matches the final AGENT_STEP's success flag.

Anything failing produces a human-readable line in `report.issues` and
flips `report.ok` to `False`.

## What `replay_against` does

Re-issues the recorded actions against a fresh `Environment`
(default: `MockEnvironment`) and compares per-step:

- The observation returned by `env.step(action)` against the recorded
  `OBSERVATION` event (with tolerance for the known mock-env "step
  counter advances after step()" gap so a clean recording roundtrips
  with zero divergence).
- The reward returned against the recorded REWARD.
- The terminal `done` flag against the recorded final-step success flag.

Any mismatch produces an entry in `drift.divergence_reasons[step_idx]`.

> **Phase 1 limitation:** live Minecraft replay is Phase 2. Pass
> `env=YourEnvironment(...)` to `replay_against` if you have a custom
> environment that satisfies the `Environment` protocol.

## Common failure modes (and what they mean)

| `report.issues` line                                       | Likely cause                                                  |
| ---------------------------------------------------------- | ------------------------------------------------------------- |
| `cot.jsonl: timestamp regressed at line N`                 | Stream was concatenated incorrectly or clocks were re-anchored |
| `step N: missing OBSERVATION in metadata.jsonl`            | Recording was truncated mid-step                               |
| `ACTION count mismatch: cot=K inputs=M`                    | One stream lost a write — bundle is incomplete                 |
| `manifest.alignment.cot_event_count=N but cot.jsonl has M` | Manifest was hand-edited or written before flush completed     |
| `orphan event OBSERVATION at T (after END T0)`             | Late writer wrote past the END marker                          |

## Determinism guarantees

- `Replayer` never modifies the bundle on disk.
- `iter_steps`, `verify_consistency`, and `replay_against` are pure
  functions of the bundle plus (for the third) the environment you pass.
- The default `MockEnvironment` is fully deterministic — same bundle,
  same env config → identical drift report.
