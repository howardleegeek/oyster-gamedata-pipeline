# Placeholder/Fake-Data Audit — 2026-05-08

## Search Scope

| Directory | File types | Patterns searched |
|-----------|-----------|-------------------|
| `bin/` | `.py`, `.sh` | placeholder, fallback, stub, fake, dummy, sample[-_]data, synthetic, sample_uuid, DEV_MODE, is_dev, dev.mode, `[0.0, 64.0, 0.0]`, TODO/FIXME near data return paths |
| `src/` | `.py` | (same) |
| `tests/` | `.py` | (same) |
| `web-tester/src/` | `.ts`, `.tsx` | DEV MODE, dev_sample, MockStripe, dev_fake, dev_local, dev_session |
| `web-buyer/src/` | `.ts`, `.tsx` | (same) |
| `mc-mod/` | `.java` | `[0.0, 64.0, 0.0]` sentinel |
| Workflows/scripts | `.sh`, `.yaml`, `.yml` | placeholder, stub, fake |

**Total matches found: ~180 lines across ~35 files**

---

## Classification Breakdown

| Category | Count | Action |
|----------|-------|--------|
| A — Already hard-fail | ~90 | No action — existing iron-law tests guard these |
| B — Intentional opt-in | ~60 | No action — behind explicit flags, metadata stamps, or test utilities |
| C — Real violations | 7 | **Fixed — all converted to hard-fail** |
| Misleading docstrings | 3 | Fixed — clarified wording |

---

## Category A — Already Hard-Fail (no action)

- `bin/recorder_consumer_lite.py` — v0.26+ `RecorderError` when game-state JSONL missing without `--allow-placeholder`
- `bin/tarball_authenticity_check.py` — entire tool purpose is detecting REAL/PLACEHOLDER/UNKNOWN
- `web-tester/` — `sample-data.ts` deleted, `NotConfigured.tsx` hard-gate, `DevModeBanner.tsx` deleted
- `web-buyer/` — `sample-data.ts` deleted, `NotConfigured.tsx` hard-gate, `DevModeBanner.tsx` deleted
- `web-tester/lib/stripe.ts` — throws when `isStripeConfigured()` is false
- `web-buyer/lib/stripe.ts` — throws when not configured
- `web-buyer/lib/catalog.ts` — throws `CatalogNotConfiguredError`
- `web-buyer/app/api/checkout/route.ts` — returns 503 when not configured
- `web-tester/app/api/download/[testerId]/route.ts` — no placeholder text response
- `web-tester/app/api/upload-tarball/route.ts` — no tmp-uploads fallback
- `bin/recorder_log_analyzer.py` — flags "sells real but ships fake"
- `bin/stamp_real_metadata.py` — stamps real vs synthetic
- `bin/spec_lint.py` — bans placeholder/mock/stub/fake in specs

## Category B — Intentional Opt-In (no action)

- `bin/recorder_consumer_lite.py` `--allow-placeholder` flag + `data_authenticity=placeholder` metadata stamp
- `bin/buyer_spec_pipeline.sh` — drops `_PLACEHOLDER_NOTICE.txt` when creating synthetic depth
- `bin/recorder_test_harness.py` — test utility, creates synthetic test data (fixtures)
- `bin/red_team/blue_team_score.py` — adversarial test suite, mock video for testing
- `bin/red_team/attackers.py` — attack simulation (test-only)
- `bin/sample_tarball_builder.py` — CLI tool explicitly for building sample tarballs (synthesize_video uses testsrc)
- `bin/recorder_replay_mod_postprocess.py` — status='stub' with clear degradation labeling
- `bin/i18n_zh_en_strings.py` — fallback strings are legitimate i18n defaults
- `bin/recorder_mc_config_reader.py` — safe defaults with warnings list when no .minecraft dir
- `bin/depth_from_mineflayer_raycast.py` — y=64 fallback IS real geometry (rays are cast from flat ground)
- `bin/c2pa_signer.py` — `is_synthetic` flag for EU AI Act disclosure compliance
- `bin/synthetic_disclosure_metadata.py` — disclosure tool by design
- `bin/imu_provider.py` — synthetic IMU data generation tool (purpose-built)
- `bin/error_dashboard_web.py` — `generate_sample_errors()` behind explicit `--sample` CLI flag
- `bin/inventory_voxel_capture.py` — demo mode behind `--demo` flag
- `bin/vendor_scenario_mac_only.py` — `dry_run=True` returns stub by design
- `bin/vendor_scenario_no_gpu.py` — legitimate GPU→CPU fallback scenario
- `bin/end_to_end_consumer_smoke.py` — mock provider trajectory (test harness)
- `bin/recorder_audio_loopback.py` — `-i dummy` is standard ffmpeg device enumeration
- `bin/recorder_consumer_lite.py:1953` — `-i dummy` is ffmpeg dshow probe
- `src/oyster_agent_runner/environments/base.py` — `MockEnvironment` is test utility
- `tests/` (all) — test fixtures, cassettes, synthetic data for testing

## Category C — Real Violations (FIXED)

### 1. `bin/optical_flow_provider.py:211-222`

**Before:** When imageio unavailable, silently generated 10 random placeholder frames.
```python
logger.warning("imageio not available, generating placeholder frames")
return self._placeholder_frames(output_dir)
```
**After:** Raises `RuntimeError` with install instructions.
```python
raise RuntimeError(
    "optical_flow_provider requires imageio to extract video frames. "
    "Install it with: pip install imageio[ffmpeg]. "
    "Iron-law: never generate placeholder frames."
)
```

### 2. `bin/depth_anything_smoke.py:66-83`

**Before:** When `depth_anything_v2` unavailable, silently returned `MockDepthModel` producing fake sine-wave depth.
```python
print("Warning: depth_anything_v2 not found, using mock model")
return MockDepthModel()
```
**After:** Raises `RuntimeError`.
```python
raise RuntimeError(
    "depth_anything_smoke requires the depth_anything_v2 package. ..."
)
```

### 3. `bin/recorder_dav2_runner.py:207-242`

**Before:** `infer_depth()` silently fell back to `_mock_depth()` (smooth ramp) when model was None or inference failed.
```python
if model is None:
    return _mock_depth(rgb)
# ... and on exception:
    return _mock_depth(rgb)
```
**After:** Raises `RuntimeError` when model is None. Removed `_mock_depth()` entirely. Exception in inference now propagates.

### 4. `bin/vendor_alpha_dashboard.py:20-57`

**Before:** `load_sample_data()` generated entirely fake metrics from `hash(vendor_id) % 1000`.
```python
vendor_hash = hash(vendor_id) % 1000
base_metrics["orders"] = 50 + (vendor_hash % 200)
base_metrics["revenue"] = float(500 + (vendor_hash * 3) % 5000)
```
**After:** Renamed to `load_vendor_metrics()`. Reads from `metrics/<vendor_id>/<date>.json`. Raises `FileNotFoundError` if file doesn't exist.

### 5. `bin/sample_tarball_builder.py:262-275`

**Before:** When OpenEXR unavailable, wrote fake 4-byte EXR magic + 512 zero bytes.
```python
f.write(b'\x76\x2f\x31\x01')  # EXR magic number
f.write(b'\x00' * 512)
```
**After:** Raises `RuntimeError` with install instructions.

### 6. `bin/sample_tarball_builder.py:324-329`

**Before:** When openpyxl unavailable, wrote fake 4-byte ZIP magic + 512 zero bytes as XLSX.
```python
f.write(b'PK\x03\x04')  # ZIP magic (xlsx is a ZIP)
f.write(b'\x00' * 512)
```
**After:** Raises `RuntimeError` with install instructions.

### 7. `bin/payout_cron.py:197-201,355-362`

**Before:** `make_stripe_client()` and `make_supabase_client()` silently returned `MockStripeClient`/`MockSupabaseClient` when env vars missing — even in production.
```python
def make_stripe_client():
    ...
    return MockStripeClient()  # silent fallback
```
**After:** Both functions now require `allow_mock=True` to return mock clients. Without it, raise `RuntimeError`. `main()` passes `allow_mock=args.dry_run`.

---

## Misleading Docstrings Fixed

| File | Line | Before | After |
|------|------|--------|-------|
| `bin/optical_flow_provider.py:112` | `_create_model` | "placeholder for actual implementation" | "Create a lightweight RAFT-style model for optical flow" |
| `bin/autoresearch_lint_perf.py:53` | `lint_buyer_spec` | "placeholder implementation" | "Lint a buyer specification file" |
| `bin/c2pa_signer.py:104` | `sign_manifest` | "placeholder for actual signing" | "Sign a C2PA manifest. Sets status='demo' when no key/cert provided" |

---

## Newly-Added Iron-Law Tests (6)

| Test | Guards against |
|------|---------------|
| `test_optical_flow_no_placeholder_frames` | `_placeholder_frames()` returning random data |
| `test_depth_anything_smoke_no_mock_model` | `MockDepthModel` returning fake sine-wave depth |
| `test_recorder_dav2_runner_no_mock_depth` | `_mock_depth()` returning ramp when model None |
| `test_vendor_alpha_dashboard_no_sample_data` | `load_sample_data()` generating hash-derived fake metrics |
| `test_sample_tarball_builder_no_fake_exr` | Raw EXR/XLSX magic bytes written as placeholders |
| `test_payout_cron_no_silent_mock_fallback` | Silent MockStripeClient/MockSupabaseClient in production |

Updated: `test_stripe_connect.py` — 3 tests adapted to `allow_mock` semantics + 1 new `test_no_env_without_allow_mock_raises`.

---

## Verification Results

| Check | Result |
|-------|--------|
| `pytest tests/test_iron_law_no_fake_data.py` | **25 passed** |
| `pytest tests/test_stripe_connect.py` | **31 passed** |
| `black --check src/ tests/` | **166 files clean** |
| `npx tsc --noEmit` (web-tester) | **clean** |
| `npx tsc --noEmit` (web-buyer) | **clean** |

---

## Human Review Required

| File | Line | Pattern | Reason |
|------|------|---------|--------|
| `bin/c2pa_signer.py:106` | `status = "demo"` | Signs manifest with demo status when no key | By design — unsigned C2PA manifests are valid. Not a silent data substitution. |
| `bin/recorder_consumer_lite.py:1606` | `"recorderVersion": "lite-v0.10.0-fallback"` | Systeminfo fallback when helper import fails | Uses real window geometry from rect dict; only recorderVersion string differs. Acceptable — documents the fallback clearly. |
| `bin/multi_camera_capture.py:131` | `generate_synthetic_frame()` | Generates labeled synthetic frames for testing | Called from `capture_single_frame()` which is used in dry-run/simulation mode. Not a silent substitution — the entire tool is a capture simulator. |
