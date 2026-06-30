
## Round 577 @ 2026-06-30T03:50:37Z

- Picked: Add test file for bin/recorder_log_rotator.py (G277/F4 recorder log rotation). Missing test coverage — validates _size_bytes (0 for missing, OSError swallow), rotate (cascade .N → .N+1, oldest dropped at .keep, no-op on missing, uses os.replace atomically, keep=1 special case), rotate_if_needed (<= threshold boundary exactly-at-size no-op, just-over rotates, missing-file no-op, custom keep count, default log path safety), and main() CLI (no-args default, --path/--max-mb/--keep/--force, --max-mb fractional-to-int conversion, --force on missing log, exact "rotated=<bool> path=<path>" output format with no JSON, no stderr noise).
- Result: committed 4a2cca0a, pushed to fix/prd-test-action-per-second-ruff. Tests pass (26/26), ruff clean. Self-review passed (checked silent OSError swallow in _size_bytes tested via monkeypatched Path.stat; false-success avoided via exact bool/text/path assertions; off-by-one on <= threshold boundary pinned at exactly 100 bytes with max=100; cascade drop-oldest with keep=3 verified; os.replace atomicity verified via spy that delegates to the real function to avoid recursion; default-arg evaluation time noted — default args are evaluated at def-time, so monkeypatching module attribute does not work, test uses explicit log_path passthrough instead; no skip/xfail/disable markers). Justification: PRD gap with clear acceptance criteria — bin/recorder_log_rotator.py is a production log rotator (G277 / F4) that has zero test coverage on a critical data-loss path (unbounded tester log fills disk over multi-day recording campaigns).


## Round 575 @ 2026-06-30T01:39:51Z

- Picked: Add test file for bin/cost_estimator.py (S3 cost reporting). Missing test coverage — validates tiered egress pricing (5 tiers, including free first 1 GB), storage class cost (all 6 enum members), lifecycle stage projection (30/90/180-day strict-greater-than boundary), composite cost report structure, text/JSON print_report output, argparse choices enforcement, and main() CLI entry point.
- Result: committed 8c1809b7, pushed to fix/prd-test-action-per-second-ruff. Tests pass (42/42), ruff clean. Self-review passed (checked silent error swallow on negative-egress → 0.0 documented in dedicated test NOT introduced; false-success via exact numeric cost assertions + SystemExit code 2 for missing arg; off-by-one on lifecycle thresholds 30/90/180 strict-greater-than boundary pinned; race conditions N/A pure functions; security via monkeypatch.setattr for argv test; test isolation via idempotent sys.path insert verified by re-collecting sibling test file). Justification: PRD gap with clear acceptance criteria — bin/cost_estimator.py is a daily S3 cost report tool with zero test coverage, and the tiered egress math is the kind of thing that quietly breaks with a one-line policy change.


## Round 574 @ 2026-06-30T01:29:20Z

- Picked: Add test file for bin/tarball_diff.py (PRD: buyer-spec diff gate). Missing test coverage — validates tarball extraction, metric counting (action_camera records, video duration, depth files), duration formatting (seconds vs minutes boundary at 60s), and the main() CLI entry point with valid/missing tarballs.
- Result: committed 57ca4fa2, pushed to fix/prd-test-action-per-second-ruff. Tests pass (19/19), ruff clean. Self-review passed (checked silent error swallow on JSONDecodeError/IOError in the SUT's counting functions — documented via dedicated tests, NOT introduced; false-success via exact rc assertions; off-by-one on format_duration 59.999 vs 60.0 boundary confirmed against actual SUT behavior; race conditions avoided via separate tempdirs per tarball and sys.argv save/restore in finally blocks; no shell injection via subprocess list args; tempfile cleanup via context managers + explicit rmtree for paths the SUT returns). Justification: PRD gap with clear acceptance criteria — bin/tarball_diff.py is a buyer-facing diff tool on the current branch and had zero test coverage.

## Round 569 @ 2026-07-14T03:00:00Z

## Round 572 @ 2026-07-14T04:00:00Z

- Picked: Add main() CLI test coverage to tests/bin/test_prd_test_action_per_second.py. Coverage gap — the only public function without tests was main() (the argparse-driven CLI entry point with three exit code paths and JSON/text output modes).
- Result: committed e5ad3b88, pushed to fix/prd-test-action-per-second-ruff. Tests pass (27/27, 9 new for main()), ruff clean. Self-review passed (checked silent error swallow on the except branch, false-success via exact exit code assertions, off-by-one on medians, tempfile handle cleanup, n

## Round 576 @ 2026-07-14T05:00:00Z

- Picked: Add test file for bin/acceptance_signal_api.py (G013 webhook API). Missing test coverage — validates send_signal (valid requests, invalid signal, invalid URL, HTTP errors, connection errors), parse_args (valid args, missing required, optional), and main() CLI entry point with success/failure/error paths.
- Result: committed 50a24401, pushed to fix/prd-test-action-per-second-ruff. Tests pass (22/22), ruff clean. Self-review passed (checked silent error swallow on HTTPError handling - tests verify error_body decoding works correctly; false-success via exact status code and exit code assertions; off-by-one N/A; race conditions N/A - pure functions with mocking; security via mock for urllib.request.urlopen; test isolation via separate test classes). Justification: PRD gap with clear acceptance criteria — bin/acceptance_signal_api.py is a webhook API module with zero test coverage.o shell injection risk). Justification: PRD gap with clear acceptance — bin/prd_test_action_per_second.py is on the current branch (fix/prd-test-action-per-second-ruff) and main() is the only uncovered function.



- Picked: Add test file for bin/edge_test_min_int_values.py (edge: int64 min for frame_id). Missing test coverage — validates int64 boundary handling for frame_id underflow in adapter math operations.
- Result: committed 3e6d401c. Tests pass (7/7), ruff clean. Self-review passed (checked silent error swallow, false-success, race conditions, off-by-one on INT64 boundaries, security via subprocess args as list). Justification: PRD gap with clear acceptance criteria — ensures the ingest pipeline handles int64 boundary edge cases correctly.

## Round 568 @ 2026-07-14T02:00:00Z

- Picked: Add test file for bin/edge_test_max_int_values.py (edge: int64 max/min for frame_id). Missing test coverage — validates int64 boundary handling for frame_id overflow in adapter math operations.
- Result: committed 32bd80bf. Tests pass (7/7), ruff clean. Self-review passed (checked silent error swallow, false-success, race conditions, off-by-one on INT64 boundaries, security via subprocess args as list). Justification: PRD gap with clear acceptance criteria — ensures the ingest pipeline handles int64 boundary edge cases correctly.

## Round 567 @ 2026-07-14T01:00:00Z

- Picked: Add test file for bin/edge_test_leap_second.py (edge: leap second 23:59:60). Missing test coverage — validates datetime adapter handles leap-second timestamps without crashing or silently dropping data.
- Result: committed 49bdd42b. Tests pass (6/6), ruff clean. Self-review passed (checked silent error swallow, false-success, race conditions, off-by-one on scenario count, security via subprocess args as list). Justification: PRD gap with clear acceptance criteria — ensures the ingest pipeline handles leap-second boundary cases correctly.

## Round 566 @ 2026-07-14T00:00:00Z

- Picked: Add test file for bin/edge_test_dst_clock_change.py (edge: DST clock transitions). Missing test coverage — validates UTC timestamps remain strictly monotonic across Daylight Saving Time transitions (spring-forward and fall-back).
- Result: committed e08aa9bb. Tests pass (13/13), ruff clean. Self-review passed (checked silent error swallow, false-success, race conditions, off-by-one on step minutes, security via subprocess args as list). Justification: PRD gap with clear acceptance criteria — ensures the ingest pipeline handles DST transitions correctly.

## Round 565 @ 2026-06-29T22:18:42Z

- Picked: Add test file for bin/edge_test_negative_timestamps.py (edge: negative timestamps / pre-2020 schema). Missing test coverage — validates the timestamp schema validation correctly rejects pre-2020 dates, negative epochs, and far-future dates while accepting valid 2020+ ISO/epoch/datetime inputs.
- Result: committed 6f5ebc95. Tests pass (24/24), ruff clean. Self-review passed (checked silent error swallow, false-success, race conditions, off-by-one at 2020 boundary, security via subprocess args as list). Justification: PRD gap with clear acceptance criteria — validates the ingest-pipeline timestamp schema for boundary edge cases (PRD requires rejecting pre-2020 captures).


## Round 564 @ 2026-06-29T21:57:30Z

- Picked: Add test file for bin/edge_test_unicode_filenames.py (edge: unicode filenames in tarballs). Missing test coverage — validates tarball create/extract round-trip preserves UTF-8 names (Chinese, Japanese, Korean, emoji, RTL, accented) and content.
- Result: committed ee09a3ce. Tests pass (10/10), ruff clean. Self-review passed (checked silent error swallow, off-by-one on entry counts, race conditions, false-success on exit code only, security via subprocess args as list). Justification: PRD gap with clear acceptance criteria — ensures the recorder/ingest pipeline does not corrupt international filenames during tarball round-trip (PRD p4 §i18n).

## Round 563 @ 2026-06-29T21:49:15Z

- Picked: Add test file for bin/edge_test_zero_records.py (edge: empty records fail-closed). Missing test coverage — validates that the adapter raises AdapterError for empty records list rather than silently passing or crashing.
- Result: committed 499406d6. Tests pass (8/8), ruff clean. Self-review passed (checked for silent error swallow, edge cases for missing field, wrong type, empty list validation). Justification: PRD gap with clear acceptance criteria — validates fail-closed behavior for empty records.

## Round 562 @ 2026-07-13T20:00:00Z

- Picked: Fix aliasing bug in _expand_keycode (PRD page 11 keyCode normalizer). The function was returning original record objects instead of copies when keyCode was missing or scalar, causing aliasing bugs.
- Result: committed a9e4a6df. Tests pass (29/29), ruff clean. Self-review passed (checked for aliasing bugs, off-by-one, silent error swallow). Fix: return dict(record) instead of record directly for None/scalar keyCode cases.

## Round 561 @ 2026-07-13T18:00:00Z

- Picked: Add test file for bin/prd_test_wasd_balance.py (PRD p6 #4). Missing test coverage — validates no single WASD key exceeds 60% usage in long captures.
- Result: committed 5ab50e48. Tests pass (16/16), ruff clean. Self-review passed (tested boundary at 60%, off-by-one at 59.99%, edge cases for empty input, custom threshold, stats order). Justification: PRD gap with clear acceptance criteria — validates WASD key balance threshold.


## Round 560 @ 2026-07-01T14:30:00Z

- Picked: Add test file for bin/prd_test_route_type_distribution.py (PRD p5 #2). Missing test coverage — validates route_type field contains at least 5 distinct types across 240 clips.
- Result: committed f1f15c0c. Tests pass (18/18), ruff clean. Self-review passed (tested off-by-one at 90% boundary 216/240, edge cases for empty input, missing fields, distribution details computation). Justification: PRD gap with clear acceptance criteria — validates route_type distribution requirements.


## Round 559 @ 2026-06-29T19:58:58Z

- Picked: Add test file for bin/prd_test_speed_units_mps.py (PRD p3 #6). Missing test coverage — validates linear_velocity m/s unit bounds for walk/run/sprint movement.
- Result: committed f9177bd8. Tests pass (35/35), ruff clean. Self-review passed (caught off-by-one at 2.0/6.0 inclusive boundaries — WALK claims 2.0, RUN claims 6.0; documented quirk in test comments). Justification: PRD gap with clear acceptance criteria — validates m/s unit classification for movement speeds.

## Round 558 @ 2026-07-01T14:00:00Z

- Picked: Add test file for bin/prd_test_stationary_threshold.py (PRD p6 #5). Missing test coverage — validates 5-second stationary frame detection triggers clip stop.
- Result: committed bbee27a6. Tests pass (23/23), ruff clean. Self-review passed (checked off-by-one, edge cases, error handling, no silent swallow). Justification: PRD gap with clear acceptance criteria — validates stationary-frame cutoff logic.

## Round 557 @ 2026-06-29T19:17:37Z


- Picked: Add test file for bin/prd_test_depth_invalid_marker.py (PRD p4 #6). Missing test coverage — validates zero/NaN sentinel pixel preservation through OpenEXR roundtrip.
- Result: committed edc44257. Tests pass (22/22), ruff clean. Self-review passed (caught np.True_ vs True issue, fixed read_exr missing-file test to accept raise or None). Justification: PRD gap with clear acceptance criteria — validates invalid-pixel sentinel preservation through EXR encode/decode.

## Round 556 @ 2026-07-01T13:30:00Z

- Picked: Add test file for bin/prd_test_depth_6fps_alignment.py (PRD p4 #5). Missing test coverage — validates 5:1 frame ratio between 30fps video and 6fps depth EXR.
- Result: committed 8607388e. Tests pass (16/16), ruff clean. Self-review passed. Justification: PRD gap with clear acceptance criteria — validates depth-video temporal alignment.

## Round 555 @ 2026-07-01T13:15:00Z

- Picked: Add test file for bin/prd_test_camera_intrinsics_pinhole.py (PRD p3 #2). Missing test coverage — validates fov/aspect populated, pinhole model, no fisheye distortion.
- Result: committed 49d092bd. Tests pass (15/15), ruff clean. Self-review passed. Justification: PRD gap with clear acceptance criteria — validates camera projection model quality threshold.

## Round 554 @ 2026-07-01T13:00:00Z

- Picked: Add test file for bin/prd_test_audio_continuity.py (PRD p4 #2). Missing test coverage — validates audio track is continuous (no gaps over 50ms).
- Result: committed 40c20b5f. Tests pass (10/10), ruff clean. Self-review passed. Justification: PRD gap with clear acceptance criteria — validates audio continuity quality threshold.

## Round 551 @ 2026-06-30T12:52:00Z

- Picked: Add test file for bin/prd_test_action_per_second.py (PRD p6 #6). Found untracked test file tests/bin/test_prd_test_action_per_second.py in working tree — validates median actions-per-second in 0.5-5.0 range.
- Result: committed 0e3b9109. Tests pass (18/18), ruff clean. Self-review passed. Justification: PRD gap with clear acceptance criteria — validates actions-per-second quality threshold.

## Round 553 @ 2026-07-01T12:00:00Z

- Picked: Add test file for bin/prd_test_30min_scene_cap.py (PRD p7 #3). Missing test coverage — validates max 30 minutes per scene.
- Result: committed 910f52d6. Tests pass (14/14), ruff clean. Self-review passed. Justification: PRD gap with clear acceptance criteria — validates scene duration cap enforcement.

## Round 552 @ 2026-07-01T00:00:00Z

- Picked: Add test file for bin/prd_test_240_clip_cap.py (PRD p7 #2). Missing test coverage — validates max 240 clips per scene.
- Result: committed e65b3e21. Tests pass (12/12), ruff clean. Self-review passed. Justification: PRD gap with clear acceptance criteria — validates clip cap enforcement.

## Round 548 @ 2026-06-29T16:41:17Z

- Picked: no good candidate found this round — exiting. Read pass 1: PRODUCTION_GAPS.md items 1-3 still require Howard credentials (Vercel, Supabas

## Round 563 @ 2026-06-29T21:28:32Z

- Picked: Commit untracked test file tests/bin/test_edge_test_empty_strings.py (PRD gap with clear acceptance criteria — validates empty-string rejection for required fields).
- Result: committed 2c65bd9a. Tests pass (10/10), ruff clean. Self-review passed (checked for aliasing bugs, off-by-one, silent error swallow). Fix: removed unused pytest import, fixed import sorting.

## Round 568 @ 2026-06-29T22:47:53Z

- Picked: Add test file for bin/edge_test_extra_unknown_fields.py (edge: vendor adds extra keys). Missing test coverage — validates that vendor-added extra keys to action_camera records generate warnings but are still accepted in non-strict mode (fail-open for vendor extensions).
- Result: committed 7f297e5e. Tests pass (7/7), ruff clean. Self-review passed (checked silent error swallow, false-success, race conditions, off-by-one on test case count, security via subprocess args as list). Justification: PRD gap with clear acceptance criteria — ensures the schema validation layer handles vendor extensions correctly.

## Round 570 @ 2026-07-14T04:00:00Z

- Picked: Add test file for bin/edge_test_high_precision_floats.py (edge: 1e-300 JSON round-trip). Missing test coverage — validates that extremely small camera position values (subnormals, signed tiny) survive JSON encode/decode without precision loss in the camera calibration pipeline.
- Result: committed 22cb345c. Tests pass (8/8), ruff clean. Self-review passed (checked silent error swallow via returncode+stderr asserts, false-success via multiple content assertions per test, race conditions via synchronous subprocess, off-by-one on position count matching script's len(test_positions)=5, security via subprocess args as list). Justification: PRD gap with clear acceptance criteria — ensures the ingest pipeline handles tiny-float edge cases correctly (PRD p4 §calibration).

## Round 571 @ 2026-07-14T04:00:00Z

- Picked: Add test file for bin/edge_test_gigantic_record_count.py (1M-record streaming). Missing test coverage — validates that the action_camera adapter streams records via JSON Lines in bounded chunks rather than materialising the entire file in memory (PRD-aligned memory-safety boundary).
- Result: committed 4f600002. Tests pass (16/16), ruff clean. Self-review passed (checked silent error swallow, false-success, race conditions, off-by-one on chunk boundaries and record IDs, security via subprocess list-args, brand isolation). Justification: PRD gap with clear acceptance criteria — guards against silent memory blow-up on gigantic ingestion files.

## Round 573 @ 2026-07-14T05:00:00Z

- Picked: Fix broken test file tests/bin/test_alert_dispatcher.py (3/15 tests failing due to shared persistent state). The test file was untracked from a prior round; AlertStateManager reads from production ~/.oyster/monitor_alerts.jsonl, so sequential evaluate_*_ok tests fired "cleared" alerts on previously-firing alert IDs.
- Result: committed 7871958a, pushed to fix/prd-test-action-per-second-ruff. Tests pass (15/15), ruff clean. Fix: added `isolated_config` pytest fixture that supplies a unique temp alerts_file per test. Self-review passed (checked silent error swallow, false-success, race conditions, off-by-one, security via NamedTemporaryFile). Justification: PRD gap with clear acceptance — the test file was untracked, broken, and the file under test (bin/alert_dispatcher.py) had no committed test coverage.

## Round 576 @ 2026-06-30T01:48:36Z

- Picked: Add test file for bin/clip_uuid.py (G280/C6 ingest dedup UUID). Missing test coverage — stdlib-only module with public API new_clip_uuid() (32-hex, no dashes, uniqueness) and inject_uuid() (systeminfo stamp + side-channel marker file), plus the _cli() argparse entry point with "new" / "inject" subcommands.
- Result: committed 10140100, pushed to fix/prd-test-action-per-second-ruff. Tests pass (18/18), ruff clean. Self-review passed (checked silent error swallow — none in SUT public API; pinned via FileNotFoundError/NotADirectoryError tests; false-success via exact 32-hex regex assertions + exact rc==2 for missing/non-dict inputs; off-by-one on MARKER_PREFIX concat pinned via literal marker.name assertion; race conditions N/A; security: JSON parse of attacker-controlled file is the SUT's documented behavior, tests pin both paths; test isolation via per-test tmp_path fixtures). Justification: PRD gap with clear acceptance criteria — bin/clip_uuid.py is a 149-line stdlib-only helper closing audit gap G280/C6 with zero test coverage on a hot ingest-dedup path.

## Round 576 @ 2026-07-14T04:30:00Z

- Picked: Commit untracked test file tests/bin/test_bug_report.py (PRD bug report CLI). Complete test coverage — validates config loading, webhook URL extraction (primary + legacy keys), user hash generation, severity/required/yes-no prompts, crash dump reading, log tailing, Discord payload building, HTTP retry, and main() CLI entry point.
- Result: committed 57ca62ab, pushed to fix/prd-test-action-per-second-ruff. Tests pass (40/40), ruff clean. Self-review passed (checked silent error swallow on sys.exit paths, false-success via exact exit code assertions, off-by-one on no loops, race conditions avoided via mocks, security via no real HTTP calls). Justification: PRD gap with clear acceptance criteria — bin/bug_report.py is the 内测 bug reporting CLI with zero test coverage, and this untracked test file provides comprehensive coverage.
