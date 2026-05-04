#!/usr/bin/env python3
"""
spec_generator.py — Safe YAML-aware spec appender for audit_gaps.yaml.

Why this exists:
    A previous string-concat append broke the YAML at line 447 col 22, halting the harness.
    This generator builds Python dicts and uses yaml.safe_dump → cannot produce invalid YAML.

Usage:
    python3 bin/spec_generator.py            # dry-run, prints what would be added
    python3 bin/spec_generator.py --apply    # actually write + validate
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
GAPS_FILE = REPO / "docs" / "audit_gaps.yaml"


# ---------------------------------------------------------------------------
# 100 atomic NEW-FILE specs across 9 wave categories.
# ---------------------------------------------------------------------------
SPECS: list[dict] = [
    # ─── W9 Edge cases (15) ───────────────────────────────────────────────
    {"id": "G039", "title": "bin/edge_test_zero_records.py",
     "purpose": "Boundary test: action_camera.json with empty records list — adapter must fail-closed not crash",
     "lines": 80, "priority": "P1"},
    {"id": "G040", "title": "bin/edge_test_negative_timestamps.py",
     "purpose": "Boundary test: timestamp 1970-01-01 negative epoch — schema must reject pre-2020 explicitly",
     "lines": 90, "priority": "P1"},
    {"id": "G041", "title": "bin/edge_test_gigantic_record_count.py",
     "purpose": "Boundary test: 1,000,000 records in single action_camera.json — confirm adapter streams not loads-all",
     "lines": 110, "priority": "P1"},
    {"id": "G042", "title": "bin/edge_test_unicode_filenames.py",
     "purpose": "Boundary test: Chinese / emoji / RTL filenames in tarball entries — verify utf-8 manifest support",
     "lines": 100, "priority": "P2"},
    {"id": "G043", "title": "bin/edge_test_missing_optional_fields.py",
     "purpose": "Boundary test: skip optional Vector4 quat field — adapter must default-fill not crash",
     "lines": 95, "priority": "P1"},
    {"id": "G044", "title": "bin/edge_test_extra_unknown_fields.py",
     "purpose": "Boundary test: vendor adds extra keys to action_camera record — lint warns but accepts",
     "lines": 90, "priority": "P2"},
    {"id": "G045", "title": "bin/edge_test_nan_inf_floats.py",
     "purpose": "Boundary test: NaN / +Inf in Vector3 — lint must reject as buyer spec requires finite floats",
     "lines": 105, "priority": "P0"},
    {"id": "G046", "title": "bin/edge_test_empty_strings.py",
     "purpose": "Boundary test: empty string for required string field (route_type) — fail-closed",
     "lines": 80, "priority": "P1"},
    {"id": "G047", "title": "bin/edge_test_max_int_values.py",
     "purpose": "Boundary test: int64 max for frame_id — confirm no overflow in adapter math",
     "lines": 85, "priority": "P2"},
    {"id": "G048", "title": "bin/edge_test_min_int_values.py",
     "purpose": "Boundary test: int64 min for frame_id — confirm no underflow",
     "lines": 85, "priority": "P2"},
    {"id": "G049", "title": "bin/edge_test_dst_clock_change.py",
     "purpose": "Boundary test: capture spans DST transition — UTC timestamps must remain monotonic",
     "lines": 100, "priority": "P1"},
    {"id": "G050", "title": "bin/edge_test_leap_second.py",
     "purpose": "Boundary test: leap-second insertion at 23:59:60 — adapter handles or rejects cleanly",
     "lines": 95, "priority": "P3"},
    {"id": "G051", "title": "bin/edge_test_high_precision_floats.py",
     "purpose": "Boundary test: 1e-300 tiny floats in camera position — verify json round-trip preserves precision",
     "lines": 90, "priority": "P2"},
    {"id": "G052", "title": "bin/edge_test_quaternion_norm_drift.py",
     "purpose": "Boundary test: quaternion magnitude 1.0001 from float drift — lint tolerates within epsilon",
     "lines": 110, "priority": "P1"},
    {"id": "G053", "title": "bin/edge_test_camera_pitch_singularity.py",
     "purpose": "Boundary test: pitch exactly 90.0 / -90.0 gimbal-lock case — adapter clamps not wraps",
     "lines": 100, "priority": "P0"},

    # ─── W10 Stress / load (5) ────────────────────────────────────────────
    {"id": "G054", "title": "bin/stress_test_50_concurrent_lints.py",
     "purpose": "Stress test: spawn 50 lint processes against same tarball — verify no shared-state corruption",
     "lines": 130, "priority": "P1"},
    {"id": "G055", "title": "bin/stress_test_huge_tarball_5gb.py",
     "purpose": "Stress test: build 5 GB tarball (long capture + 6fps depth) — verify upload_s3.sh chunked path holds",
     "lines": 140, "priority": "P1"},
    {"id": "G056", "title": "bin/stress_test_long_capture_2h.py",
     "purpose": "Stress test: simulate 2-hour scene with 30-min cap — confirm scene_id rotation and clip cap enforced",
     "lines": 150, "priority": "P1"},
    {"id": "G057", "title": "bin/stress_test_burst_50_clips.py",
     "purpose": "Stress test: 50 clips per minute burst — lint queue must drain without deadlock",
     "lines": 120, "priority": "P2"},
    {"id": "G058", "title": "bin/stress_test_memory_leak_check.py",
     "purpose": "Stress test: 1000-iteration adapter run with tracemalloc — assert RSS growth less than 50MB",
     "lines": 130, "priority": "P1"},

    # ─── W11 Vendor scenarios (10) ────────────────────────────────────────
    {"id": "G059", "title": "bin/vendor_scenario_first_clip.py",
     "purpose": "Walkthrough: vendor follows README from zero, uploads first clip — measure time-to-first-clip metric",
     "lines": 140, "priority": "P0"},
    {"id": "G060", "title": "bin/vendor_scenario_china_mirror.py",
     "purpose": "Walkthrough: vendor in CN behind GFW — pip mirror + S3 region selection works without VPN",
     "lines": 130, "priority": "P1"},
    {"id": "G061", "title": "bin/vendor_scenario_resume_after_crash.py",
     "purpose": "Walkthrough: kill capture mid-clip, restart — confirm no partial tarball poisons s3 + manifest detects gap",
     "lines": 150, "priority": "P0"},
    {"id": "G062", "title": "bin/vendor_scenario_low_bandwidth.py",
     "purpose": "Walkthrough: 200 Kbps upload — chunked retry succeeds within 30 min budget",
     "lines": 130, "priority": "P1"},
    {"id": "G063", "title": "bin/vendor_scenario_low_disk.py",
     "purpose": "Walkthrough: 1 GB free disk — capture pre-flight check warns and aborts safely",
     "lines": 110, "priority": "P1"},
    {"id": "G064", "title": "bin/vendor_scenario_mac_only.py",
     "purpose": "Walkthrough: vendor on M1 Mac no GPU — depth provider falls back to CPU model",
     "lines": 120, "priority": "P2"},
    {"id": "G065", "title": "bin/vendor_scenario_old_python_310.py",
     "purpose": "Walkthrough: vendor on Ubuntu 22.04 default Python 3.10 — adapter runs without datetime.UTC",
     "lines": 100, "priority": "P0"},
    {"id": "G066", "title": "bin/vendor_scenario_no_gpu.py",
     "purpose": "Walkthrough: no CUDA / Metal — DepthAnything inference falls back to onnx-cpu within SLA",
     "lines": 130, "priority": "P1"},
    {"id": "G067", "title": "bin/vendor_scenario_alpha_week.py",
     "purpose": "Walkthrough: alpha-week first 50 vendors — concurrent ingest + per-vendor quota holds",
     "lines": 140, "priority": "P1"},
    {"id": "G068", "title": "bin/vendor_scenario_rejection_loop.py",
     "purpose": "Walkthrough: vendor uploads malformed clip — rejection email + retry guide reaches them",
     "lines": 130, "priority": "P1"},

    # ─── W12 PRD-deep coverage (15) ───────────────────────────────────────
    {"id": "G069", "title": "bin/prd_test_video_no_ui.py",
     "purpose": "PRD p4 #3: video must contain no overlay UI / chat / dialogs — OCR scan asserts clean frames",
     "lines": 140, "priority": "P0"},
    {"id": "G070", "title": "bin/prd_test_route_type_distribution.py",
     "purpose": "PRD p5 #2: route_type field across 240 clips must hit at least 5 distinct types — distribution check",
     "lines": 110, "priority": "P1"},
    {"id": "G071", "title": "bin/prd_test_wasd_balance.py",
     "purpose": "PRD p6 #4: WASD action balance — no single key over 60 percent in long captures",
     "lines": 100, "priority": "P1"},
    {"id": "G072", "title": "bin/prd_test_stationary_threshold.py",
     "purpose": "PRD p6 #5: stationary frames over 5s must trigger clip stop — verify cutoff",
     "lines": 110, "priority": "P0"},
    {"id": "G073", "title": "bin/prd_test_action_per_second.py",
     "purpose": "PRD p6 #6: median actions-per-second 0.5 to 5.0 — out-of-band capture flagged as low-quality",
     "lines": 100, "priority": "P1"},
    {"id": "G074", "title": "bin/prd_test_camera_intrinsics_pinhole.py",
     "purpose": "PRD p3 #2: camera projection must be pinhole — fov + aspect populated, no fisheye distortion params",
     "lines": 120, "priority": "P0"},
    {"id": "G075", "title": "bin/prd_test_left_hand_coordinates.py",
     "purpose": "PRD p3 #4: left-hand coordinate system — assert handedness via cross-product sign on Vector3 axes",
     "lines": 100, "priority": "P0"},
    {"id": "G076", "title": "bin/prd_test_metric_units_meters.py",
     "purpose": "PRD p3 #5: positions in meters — sanity-bound camera_position within world cube radius",
     "lines": 95, "priority": "P1"},
    {"id": "G077", "title": "bin/prd_test_speed_units_mps.py",
     "purpose": "PRD p3 #6: linear_velocity in m/s — bound to player walk run sprint speeds",
     "lines": 100, "priority": "P1"},
    {"id": "G078", "title": "bin/prd_test_240_clip_cap.py",
     "purpose": "PRD p7 #2: max 240 clips per scene — adapter stops at 241st",
     "lines": 90, "priority": "P0"},
    {"id": "G079", "title": "bin/prd_test_30min_scene_cap.py",
     "purpose": "PRD p7 #3: max 30 min per scene — clock cap enforced",
     "lines": 95, "priority": "P0"},
    {"id": "G080", "title": "bin/prd_test_systeminfo_required.py",
     "purpose": "PRD p7 file 1: required keys (gpu, cpu, ram_gb, os, build) all present — fail-closed if missing",
     "lines": 100, "priority": "P0"},
    {"id": "G081", "title": "bin/prd_test_depth_invalid_marker.py",
     "purpose": "PRD p4 #6: depth invalid pixel sentinel value (zero or NaN) preserved through OpenEXR roundtrip",
     "lines": 110, "priority": "P0"},
    {"id": "G082", "title": "bin/prd_test_depth_6fps_alignment.py",
     "purpose": "PRD p4 #5: depth EXR 6fps alignment with 30fps video — frame index ratio 5:1 exact",
     "lines": 105, "priority": "P0"},
    {"id": "G083", "title": "bin/prd_test_audio_continuity.py",
     "purpose": "PRD p4 #2: video audio track continuous (no gaps over 50ms) — ffprobe + numpy diff check",
     "lines": 120, "priority": "P1"},

    # ─── W13 Red team adversarial (15) ────────────────────────────────────
    {"id": "G084", "title": "bin/red_team_oversized_json.py",
     "purpose": "Red team: 100 MB action_camera.json — confirm adapter rejects or streams without OOM",
     "lines": 110, "priority": "P0"},
    {"id": "G085", "title": "bin/red_team_nan_coordinates.py",
     "purpose": "Red team: inject NaN into camera_position — lint must reject not silently propagate",
     "lines": 90, "priority": "P0"},
    {"id": "G086", "title": "bin/red_team_year_9999_timestamp.py",
     "purpose": "Red team: timestamp year 9999 — schema clamps within sane window",
     "lines": 85, "priority": "P1"},
    {"id": "G087", "title": "bin/red_team_path_traversal.py",
     "purpose": "Red team: tarball entry name contains ../../etc — extractor must refuse traversal",
     "lines": 100, "priority": "P0"},
    {"id": "G088", "title": "bin/red_team_duplicate_frame_id.py",
     "purpose": "Red team: two records with same frame_id — lint detects dupe and rejects",
     "lines": 95, "priority": "P1"},
    {"id": "G089", "title": "bin/red_team_concurrent_writers.py",
     "purpose": "Red team: two adapter procs write same tarball simultaneously — file lock prevents corruption",
     "lines": 130, "priority": "P0"},
    {"id": "G090", "title": "bin/red_team_sigkill_mid_write.py",
     "purpose": "Red team: SIGKILL adapter mid action_camera write — atomic temp+rename leaves no half-file",
     "lines": 120, "priority": "P0"},
    {"id": "G091", "title": "bin/red_team_disk_full.py",
     "purpose": "Red team: simulate ENOSPC during write — adapter aborts cleanly with clear error",
     "lines": 110, "priority": "P0"},
    {"id": "G092", "title": "bin/red_team_out_of_order_frames.py",
     "purpose": "Red team: shuffle frame ordering in JSON — lint rejects non-monotonic frame_id sequence",
     "lines": 90, "priority": "P1"},
    {"id": "G093", "title": "bin/red_team_wrong_obs_key.py",
     "purpose": "Red team: WebSocket auth with wrong password — session refuses + audit log records attempt",
     "lines": 100, "priority": "P1"},
    {"id": "G094", "title": "bin/red_team_corrupt_exr.py",
     "purpose": "Red team: zero-fill 1 KB inside EXR — validator detects corruption via numpy isnan/isinf scan",
     "lines": 110, "priority": "P0"},
    {"id": "G095", "title": "bin/red_team_invalid_systeminfo.py",
     "purpose": "Red team: systeminfo.json missing required gpu key — lint v2 fails closed",
     "lines": 80, "priority": "P0"},
    {"id": "G096", "title": "bin/red_team_wrong_fps.py",
     "purpose": "Red team: video tagged 60fps — lint rejects (PRD requires exactly 30fps)",
     "lines": 90, "priority": "P0"},
    {"id": "G097", "title": "bin/red_team_mixed_vector_format.py",
     "purpose": "Red team: same file mixes dict and list Vector3 forms — lint rejects format inconsistency",
     "lines": 100, "priority": "P1"},
    {"id": "G098", "title": "bin/red_team_clock_skew.py",
     "purpose": "Red team: capture machine clock jumps backward 1 hour — adapter switches to monotonic clock",
     "lines": 110, "priority": "P0"},

    # ─── W14 Blue team defense (paired hardening) (15) ────────────────────
    {"id": "G099", "title": "src/oyster_agent_runner/defense_size_limit.py",
     "purpose": "Blue team for G084: per-file size cap defender (10 MB action_camera, 500 MB video)",
     "lines": 110, "priority": "P0"},
    {"id": "G100", "title": "src/oyster_agent_runner/defense_finite_check.py",
     "purpose": "Blue team for G085: assert_finite helper running on every Vector3 value before write",
     "lines": 90, "priority": "P0"},
    {"id": "G101", "title": "src/oyster_agent_runner/defense_timestamp_range.py",
     "purpose": "Blue team for G086: enforce timestamps within 2024-01-01 to 2030-01-01 range",
     "lines": 80, "priority": "P1"},
    {"id": "G102", "title": "src/oyster_agent_runner/defense_path_sanitize.py",
     "purpose": "Blue team for G087: tarball path sanitizer rejecting absolute / traversal members",
     "lines": 100, "priority": "P0"},
    {"id": "G103", "title": "src/oyster_agent_runner/defense_dedup_frames.py",
     "purpose": "Blue team for G088: in-memory set tracker rejecting duplicate frame_id within scene",
     "lines": 90, "priority": "P1"},
    {"id": "G104", "title": "src/oyster_agent_runner/defense_file_lock.py",
     "purpose": "Blue team for G089: fcntl flock wrapper around tarball open — prevents concurrent corruption",
     "lines": 110, "priority": "P0"},
    {"id": "G105", "title": "src/oyster_agent_runner/defense_atomic_write.py",
     "purpose": "Blue team for G090: write_atomic helper using tempfile + os.replace",
     "lines": 95, "priority": "P0"},
    {"id": "G106", "title": "src/oyster_agent_runner/defense_disk_check.py",
     "purpose": "Blue team for G091: pre-flight shutil.disk_usage check requiring 5 GB free before capture",
     "lines": 90, "priority": "P0"},
    {"id": "G107", "title": "src/oyster_agent_runner/defense_frame_order.py",
     "purpose": "Blue team for G092: streaming validator asserting frame_id strictly increases by 1",
     "lines": 100, "priority": "P1"},
    {"id": "G108", "title": "src/oyster_agent_runner/defense_obs_auth.py",
     "purpose": "Blue team for G093: SHA256+base64 OBS auth helper with rate-limit on failed attempts",
     "lines": 110, "priority": "P1"},
    {"id": "G109", "title": "src/oyster_agent_runner/defense_exr_validate.py",
     "purpose": "Blue team for G094: post-write EXR validator scanning for NaN clusters and shape mismatch",
     "lines": 120, "priority": "P0"},
    {"id": "G110", "title": "src/oyster_agent_runner/defense_systeminfo_required.py",
     "purpose": "Blue team for G095: systeminfo schema with REQUIRED_KEYS list and pydantic-lite validator",
     "lines": 100, "priority": "P0"},
    {"id": "G111", "title": "src/oyster_agent_runner/defense_fps_strict.py",
     "purpose": "Blue team for G096: ffprobe wrapper asserting r_frame_rate exactly 30/1",
     "lines": 90, "priority": "P0"},
    {"id": "G112", "title": "src/oyster_agent_runner/defense_vector_uniform.py",
     "purpose": "Blue team for G097: scan first record format and enforce uniform Vector3 shape across file",
     "lines": 100, "priority": "P1"},
    {"id": "G113", "title": "src/oyster_agent_runner/defense_monotonic_clock.py",
     "purpose": "Blue team for G098: prefer time.monotonic_ns inside capture loop, wall clock only for stamp",
     "lines": 100, "priority": "P0"},

    # ─── W15 Autoresearch bench (10) ──────────────────────────────────────
    {"id": "G114", "title": "bin/autoresearch_lint_perf.py",
     "purpose": "Autoresearch: benchmark lint_buyer_spec on 100-tarball corpus — surface p50 / p95 / p99",
     "lines": 130, "priority": "P1"},
    {"id": "G115", "title": "bin/autoresearch_adapter_quality.py",
     "purpose": "Autoresearch: golden-corpus diff vs hand-labeled mineflayer scenes — coverage and recall metrics",
     "lines": 150, "priority": "P1"},
    {"id": "G116", "title": "bin/autoresearch_compression_ratio.py",
     "purpose": "Autoresearch: compare H.264 vs H.265 vs AV1 sizes on same scene — recommend codec",
     "lines": 130, "priority": "P2"},
    {"id": "G117", "title": "bin/autoresearch_depth_quality.py",
     "purpose": "Autoresearch: DepthAnything V2 vs Marigold MAE on 50 ground-truth Minecraft Z-buffer frames",
     "lines": 160, "priority": "P1"},
    {"id": "G118", "title": "bin/autoresearch_failure_modes.py",
     "purpose": "Autoresearch: enumerate top 10 lint failure modes from 1000 vendor tarballs — drives spec backlog",
     "lines": 140, "priority": "P1"},
    {"id": "G119", "title": "bin/autoresearch_recovery_time.py",
     "purpose": "Autoresearch: kill-9 then restart adapter — measure mean time to first new clip after crash",
     "lines": 110, "priority": "P1"},
    {"id": "G120", "title": "bin/autoresearch_throughput.py",
     "purpose": "Autoresearch: clips-per-vendor-per-day at 50 / 200 / 1000 vendors — capacity planning",
     "lines": 130, "priority": "P1"},
    {"id": "G121", "title": "bin/autoresearch_clip_density.py",
     "purpose": "Autoresearch: action density across scene types (combat / build / explore) — diversity metric",
     "lines": 120, "priority": "P2"},
    {"id": "G122", "title": "bin/autoresearch_data_diversity.py",
     "purpose": "Autoresearch: biome / time-of-day / weather distribution per 1000 clips — flag undersampled",
     "lines": 130, "priority": "P2"},
    {"id": "G123", "title": "bin/autoresearch_action_entropy.py",
     "purpose": "Autoresearch: Shannon entropy of action stream — low entropy suggests AFK / scripted vendor",
     "lines": 110, "priority": "P1"},

    # ─── W16 Production hardening (10) ────────────────────────────────────
    {"id": "G124", "title": "bin/observability_metrics_emitter.py",
     "purpose": "Production: prometheus-style counters / histograms emitted to stdout for adapter / lint / upload",
     "lines": 140, "priority": "P1"},
    {"id": "G125", "title": "bin/health_check_endpoint.py",
     "purpose": "Production: thin HTTP server reporting last_clip_at / disk_free / queue_depth — for ops",
     "lines": 120, "priority": "P1"},
    {"id": "G126", "title": "bin/audit_log_writer.py",
     "purpose": "Production: append-only newline-delimited JSON audit log of every capture / lint / upload",
     "lines": 110, "priority": "P0"},
    {"id": "G127", "title": "bin/idempotency_token.py",
     "purpose": "Production: per-clip uuid with at-least-once dedup on backend ingest path",
     "lines": 100, "priority": "P0"},
    {"id": "G128", "title": "bin/circuit_breaker.py",
     "purpose": "Production: trip after N consecutive S3 failures — halt uploads and alert",
     "lines": 130, "priority": "P1"},
    {"id": "G129", "title": "bin/rate_limiter.py",
     "purpose": "Production: token-bucket per vendor key — enforce per-day clip budget on adapter side",
     "lines": 110, "priority": "P1"},
    {"id": "G130", "title": "bin/graceful_shutdown.py",
     "purpose": "Production: SIGTERM handler flushes in-flight writes + closes tarball before exit",
     "lines": 100, "priority": "P0"},
    {"id": "G131", "title": "bin/recovery_orchestrator.py",
     "purpose": "Production: on startup, scan staging dir for half-baked tarballs and either resume or quarantine",
     "lines": 140, "priority": "P0"},
    {"id": "G132", "title": "bin/secret_rotator.py",
     "purpose": "Production: 90-day automatic S3 key rotation with overlap window — zero-downtime",
     "lines": 130, "priority": "P1"},
    {"id": "G133", "title": "bin/dependency_pinning_check.py",
     "purpose": "Production: assert all pip deps pinned to exact version — fail CI if any range",
     "lines": 90, "priority": "P1"},

    # ─── W17 Customer polish (5) ──────────────────────────────────────────
    {"id": "G134", "title": "bin/installer_one_click.py",
     "purpose": "Customer: single bash script that detects OS / installs deps / sets up PATH for vendors",
     "lines": 150, "priority": "P0"},
    {"id": "G135", "title": "bin/error_message_translator.py",
     "purpose": "Customer: convert internal exception traces into vendor-friendly remediation message",
     "lines": 120, "priority": "P1"},
    {"id": "G136", "title": "bin/onboarding_smoke_test.py",
     "purpose": "Customer: post-install verify — captures 10s clip and lints, reports go / no-go",
     "lines": 130, "priority": "P0"},
    {"id": "G137", "title": "bin/uninstall_clean.py",
     "purpose": "Customer: remove all installed files and config — leaves zero trace including launchd plists",
     "lines": 110, "priority": "P2"},
    {"id": "G138", "title": "bin/diag_bundle_collector.py",
     "purpose": "Customer: gather logs / systeminfo / last 3 manifests into a tarball for support tickets",
     "lines": 130, "priority": "P1"},
]


def build_entries() -> list[dict]:
    out = []
    for s in SPECS:
        entry = {
            "id": s["id"],
            "title": s["title"],
            "purpose": s["purpose"],
            "status": "pending",
            "priority": s["priority"],
            "lines_estimate": s["lines"],
        }
        out.append(entry)
    return out


def main(apply: bool = False) -> int:
    data = yaml.safe_load(GAPS_FILE.read_text())
    existing_ids = {g["id"] for g in data.get("gaps", [])}
    new_entries = [e for e in build_entries() if e["id"] not in existing_ids]

    print(f"existing gaps: {len(data.get('gaps', []))}")
    print(f"new specs to add: {len(new_entries)} (skipping {len(SPECS) - len(new_entries)} dupes)")
    if not new_entries:
        print("nothing to add")
        return 0

    print("first 3 new entries (preview):")
    for e in new_entries[:3]:
        print(f"  {e['id']}: {e['title']}  ({e['priority']})")
    print(f"  ... and {len(new_entries) - 3} more")

    if not apply:
        print("\n(dry-run; pass --apply to write)")
        return 0

    data.setdefault("gaps", []).extend(new_entries)
    # safe_dump avoids the manual quoting hell that broke the file last time.
    out = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120)
    GAPS_FILE.write_text(out)

    # Re-validate immediately so we never push broken YAML.
    yaml.safe_load(GAPS_FILE.read_text())
    print(f"\nwrote {GAPS_FILE} successfully ({len(data['gaps'])} gaps total)")
    return 0


if __name__ == "__main__":
    sys.exit(main(apply="--apply" in sys.argv))
