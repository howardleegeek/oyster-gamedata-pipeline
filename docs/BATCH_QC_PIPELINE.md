# Batch QC Pipeline — Operator Playbook

Ingest 100–3 000 clips per month, surface anomalies, audio quality
issues, and cross-clip scene-continuity violations before paying vendors.

This runbook covers the three tools shipped under
[`bin/`](../bin):

| Tool | Spec | Purpose |
|------|------|---------|
| `audio_qc_extractor.py` | G196 | Per-clip audio quality report |
| `anomaly_detector_clip_quality.py --batch` | G194 | Cross-clip 3 σ metric outliers |
| `multi_clip_stitcher.py --continuity` | G197 | Scene-continuity + duplicate-clip detection |

All three write structured JSON (and CSV where applicable) so they
compose cleanly into a CI / cron pipeline.

---

## 0. Prerequisites

```bash
# Required system packages
ffmpeg --version       # 4.x+ — used by audio extractor
ffprobe --version

# Python
python3 -m pip install -e '.[test]'   # pulls numpy, pyyaml, openpyxl, etc.
```

Tests run on macOS without GPU. The pipeline is cross-platform; the
audio extractor falls back to "no-op-with-violation" if ffmpeg is
unavailable so batch processing keeps going.

---

## 1. Per-clip audio QC (G196)

For each incoming clip:

```bash
python3 bin/audio_qc_extractor.py \
    /uploads/vendor-001/clip-00042_v1.tar.gz
```

Outputs `audio_qc_report.json` next to the input. Fields:

| Field | Meaning |
|-------|---------|
| `status` | `ok` / `fail` (any violation = fail) |
| `duration_s` | Total audio duration |
| `sample_rate` | Hz |
| `sample_rate_ok` | True for standard rates ≥22.05 kHz |
| `silence_runs` | Array of `{start_s, end_s, duration_ms}` for runs > 2 s |
| `sustained_silence_violation` | True if any run > 2 s |
| `clipping.clip_ratio` | Fraction of samples ≥ 0.999 amplitude |
| `clipping.saturated` | True when `clip_ratio > 0.01` |
| `dialogue.voice_band_ratio` | Fraction of frames dominated by 300–3 400 Hz |
| `dialogue.sustained_dialogue` | True when ratio > 0.5 (NPC spam) |
| `mix.bgm_energy_ratio` | 60–500 Hz energy / total |
| `mix.sfx_event_count` | Number of transient impulses |
| `violations` | List of human-readable violation tags |

Exit codes: `0` = pass, `2` = quality gate failure (report written),
`1` = unrecoverable error (missing ffmpeg, bad input).

The same checks are wired into `lint_v3_prd_grounded.py` criterion 7
("Audio Quality") — running the buyer lint on a clip automatically
invokes G196 on its `audio.wav` / `audio.flac` / `video.mp4`.

### Performance

Tested at **~0.8 s for a 6-minute clip on a developer Mac**. The 30 s
per-clip budget on minipc hardware has ~40× headroom.

---

## 2. Batch outlier detection (G194)

After the per-clip QC pass, aggregate metric snapshots into a batch
directory (one JSON per clip):

```json
{
  "clip_id": "vendor-001_batch-2026-05-A_clip-00042_v1",
  "metrics": {
    "avg_fps": 29.94,
    "file_size_mb": 812.3,
    "depth_invalid_ratio": 0.03,
    "action_entropy": 2.41
  }
}
```

Then run:

```bash
python3 bin/anomaly_detector_clip_quality.py \
    --batch /uploads/vendor-001/batch-2026-05-A/metrics/ \
    --output-json anomalies.json \
    --output-csv anomalies.csv \
    --sigma 3.0
```

`anomalies.json` shape:

```json
{
  "tool": "anomaly_detector_clip_quality",
  "mode": "batch_outlier",
  "clip_count": 248,
  "outlier_count": 3,
  "sigma": 3.0,
  "metrics": ["avg_fps", "file_size_mb", "depth_invalid_ratio", "action_entropy"],
  "outliers": [
    {
      "clip_id": "vendor-001_batch-2026-05-A_clip-00197_v1",
      "outlier_metrics": {
        "depth_invalid_ratio": {
          "value": 0.42, "mean": 0.029, "std": 0.011, "z_score": 35.6
        }
      }
    }
  ]
}
```

Exit code: `0` when no outliers, `1` otherwise — CI-friendly.

### Tuning σ

* `--sigma 2.0` — tight, will catch milder deviations (more false positives).
* `--sigma 3.0` — default, standard 3 σ.
* `--sigma 4.0` — loose, only catastrophic outliers.

Metrics with zero variance across the batch are silently skipped (no
division-by-zero NaN).

### Custom metric set

```bash
python3 bin/anomaly_detector_clip_quality.py \
    --batch metrics/ --metric avg_fps --metric file_size_mb \
    --output-json fps_size_only.json
```

---

## 3. Scene-continuity + duplicate detection (G197)

PRD §3.1 caps a single map scene at **30 minutes per operator**. We
also flag clips that share the same content hash *and* start time —
strong signal of re-submitted recordings (fraud).

Aggregate clip metadata into a JSON list (or one JSON per clip in a
directory):

```json
[
  {
    "clip_id": "...", "operator_id": "vendor-001-op-A",
    "scene_id": "flat-overworld-1.20.4",
    "duration_s": 300.0,
    "content_hash": "sha256:abcd...",
    "start_time": 1715600000.0
  }
]
```

Then run:

```bash
python3 bin/multi_clip_stitcher.py --continuity \
    --metadata clip_metadata.json \
    --output scene_continuity_report.json
```

`scene_continuity_report.json` shape:

```json
{
  "tool": "multi_clip_stitcher",
  "mode": "continuity",
  "cap_seconds": 1800,
  "total_duration_s": 12450.0,
  "scene_minute_cap_violation": true,
  "groups": [
    {
      "operator_id": "vendor-001-op-A",
      "scene_id": "flat-overworld-1.20.4",
      "clip_ids": ["...", "...", "..."],
      "clip_count": 7,
      "duration_s": 2100.0,
      "violation": true
    }
  ],
  "duplicates": [
    {
      "content_hash": "sha256:abcd...",
      "start_time": 1715600000.0,
      "clip_ids": ["clip-00042", "clip-00198"],
      "count": 2
    }
  ]
}
```

Exit code: `0` clean, `1` violation found.

### Tuning the cap

For non-MC games or special-permission batches:

```bash
python3 bin/multi_clip_stitcher.py --continuity \
    --metadata metas.json --cap-seconds 3600 \
    --output report.json
```

---

## 4. End-to-end batch pipeline

```bash
#!/usr/bin/env bash
set -euo pipefail

BATCH=/uploads/vendor-001/batch-2026-05-A
QC_OUT=$BATCH/qc

mkdir -p "$QC_OUT/audio" "$QC_OUT/metrics"

# 1. per-clip audio QC
for tar in "$BATCH"/clips/*.tar.gz; do
    name=$(basename "$tar" .tar.gz)
    python3 bin/audio_qc_extractor.py "$tar" \
        --output "$QC_OUT/audio/$name.audio_qc.json" \
        || echo "WARN audio QC fail: $name"
done

# 2. aggregate per-clip metrics into one JSON file per clip
#    (assume your post-processor emits this — e.g. bin/clip_validator_strict.py)
cp "$BATCH"/metrics/*.json "$QC_OUT/metrics/"

# 3. cross-clip 3σ outliers
python3 bin/anomaly_detector_clip_quality.py \
    --batch "$QC_OUT/metrics" \
    --output-json "$QC_OUT/anomalies.json" \
    --output-csv "$QC_OUT/anomalies.csv" \
    --sigma 3.0 || true   # rc=1 just means there are outliers

# 4. scene continuity + duplicates
python3 bin/multi_clip_stitcher.py --continuity \
    --metadata "$QC_OUT/metrics" \
    --output "$QC_OUT/scene_continuity.json" || true

# 5. roll-up
python3 - <<'PY'
import json, pathlib
qc = pathlib.Path("$QC_OUT")
an = json.loads((qc / "anomalies.json").read_text())
sc = json.loads((qc / "scene_continuity.json").read_text())
print(f"outliers: {an['outlier_count']} / {an['clip_count']}")
print(f"scene_cap_violation: {sc['scene_minute_cap_violation']}")
print(f"duplicate_groups: {len(sc['duplicates'])}")
PY
```

Wire this into your nightly cron — pay vendors only after the batch
passes all four gates (per-clip lint, per-clip audio QC, batch 3 σ
outlier check, scene-continuity check).

---

## 5. Troubleshooting

### `audio_qc_extractor.py` says `no_audio_stream`
The vendor's `video.mp4` was muxed without an audio track. Sample
tarballs (`samples/buyer-spec-v1-rc1.tar.gz`) are intentionally video-only
placeholders — this is the expected outcome there. For real submissions
this is a PRD violation; reject the clip.

### `audio_qc_extractor.py` says `sustained_npc_dialogue`
Real voice / NPC chatter is filling > 50 % of the clip duration. Either
the recording captured an in-game dialogue scene (operator error — they
should walk away from chatty NPCs) or someone played a YouTube video
through their speakers. Reject or ask for a re-record.

### `--sigma 3.0` is producing zero outliers on a batch we know is bad
The bad clips may share a common defect — meaning the *mean* is also
shifted, so individual z-scores stay small. Two remedies:

1. Drop σ to 2.0 and inspect the borderline cases manually.
2. Run cross-batch comparisons by appending the current batch metrics
   to a rolling 30-day baseline JSON before invoking the detector.

### Scene-continuity report shows duplicates but they're legitimate re-uploads
The content hash + start time match is a strong signal but not a death
warrant. Confirm by:

```bash
ls -la "$BATCH"/clips/<clip_id>.tar.gz
sha256sum "$BATCH"/clips/<clip_id>.tar.gz
```

If the operator was asked to re-upload a fixed clip, the old hash will
persist in your baseline. Either purge the prior submission from the
metadata feed or whitelist via a separate batch.

---

## 6. References

* PRD: [`docs/PRD.md`](./PRD.md), especially §3.1 video/audio specs and
  §6 acceptance criteria.
* Lint v3 (24-criteria buyer lint):
  [`bin/lint_v3_prd_grounded.py`](../bin/lint_v3_prd_grounded.py).
* Audio infra:
  [`bin/audio_event_track.py`](../bin/audio_event_track.py),
  [`bin/audio_track_extractor.py`](../bin/audio_track_extractor.py),
  [`bin/prd_test_audio_continuity.py`](../bin/prd_test_audio_continuity.py).
* Specs: G194 (anomaly detection), G196 (audio QC extractor),
  G197 (multi-clip stitcher).
