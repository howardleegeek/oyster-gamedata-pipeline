# Quickstart — Buyer Data Pipeline

> **Audience**: Buyers receiving a `.tar.gz` data bundle.
> **Time**: ≤ 5 minutes.
> **Prerequisites**: Python 3.10+ only. No other dependencies required.
> **Release**: v0.4.1 · 2026-05-19

---

## 1. Install Python 3.10+

Verify your Python version:

```bash
python3 --version
```

You need **Python 3.10 or newer**. If not installed, use your system package manager:

```bash
# macOS
brew install python@3.12

# Ubuntu / Debian
sudo apt install python3.12

# Windows — download from python.org
```

No pip packages, virtual environments, or other tools are required for verification.

---

## 2. Download the Bundle

Obtain the `.tar.gz` bundle from your vendor. Extract it:

```bash
tar xzf gamedata-bundle-*.tar.gz
cd gamedata-bundle-*/
```

The bundle contains:
- `data/` — session data files
- `verify.sh` — integrity verification script
- `manifest.json` — signed batch manifest
- `README.md` — bundle contents description

---

## 3. Run Verification

```bash
bash verify.sh
```

This script:
1. Checks SHA-256 checksums of all data files
2. Verifies the Ed25519 signature on the manifest
3. Confirms the pubkey fingerprint matches the expected vendor key

### Interpreting the Result

- **`exit 0`** — Data is **trusted and verified**. All checksums and signatures match.
- **`exit 1`** — Verification **failed**. Hash mismatch or invalid signature. Do not trust this data.
- **`exit 2`** — Pubkey fingerprint mismatch. The bundle was signed by an unexpected key.

> **Rule of thumb**: `exit 0` = data可信 (data is trustworthy). Any non-zero exit = reject the bundle.

### provenance_verify.py Reference

```
usage: provenance_verify.py [-h] [--expect-pubkey EXPECT_PUBKEY]
                            signed_manifest

Verify an Ed25519-signed batch manifest

positional arguments:
  signed_manifest       Path to signed manifest JSON

options:
  -h, --help            show this help message and exit
  --expect-pubkey EXPECT_PUBKEY
                        Expected pubkey fingerprint (first 16 hex chars of
                        sha256(pubkey))
```

---

## 4. (Optional) Run End-to-End Gate Smoke Test

For deeper validation, run the full gate suite against a session directory:

```bash
python3 bin/end_to_end_gate_smoke.py <session_dir> --strict-buyer
```

### Flags

| Flag | Description |
|---|---|
| `-h, --help` | show this help message and exit |
| `--json` | Output JSON instead of human-readable table |
| `--skip-sign` | Skip B2 provenance sign/verify round-trip |
| `--strict-buyer` | v0.4.1: BLOCK on SKIP/PASS_DEGRADED for H8/S1/V1/V2/B2 |

### Gates Checked

| Gate | Label | What It Checks |
|---|---|---|
| H8 | Depth source | Engine Z-buffer, EXR format validity |
| S1 | Sync tolerance | Frame sync within 50ms threshold |
| S2 | Input latency | Input event timing accuracy |
| V1 | Video quality | Video artifact detection |
| V2 | Video artifacts | Additional video quality checks |
| B2 | Provenance | Ed25519 signature verification |

All gates must **PASS** for a production buyer deliverable when `--strict-buyer` is used.

### end_to_end_gate_smoke.py Reference

```
usage: end_to_end_gate_smoke.py [-h] [--json] [--skip-sign] [--strict-buyer]
                                session_dir

End-to-end gate smoke test — runs all gates against a session dir

positional arguments:
  session_dir     Path to session directory

options:
  -h, --help      show this help message and exit
  --json          Output JSON instead of human-readable table
  --skip-sign     Skip B2 provenance sign/verify round-trip
  --strict-buyer  v0.4.1: BLOCK on SKIP/PASS_DEGRADED for H8/S1/V1/V2/B2
                  gates. Required for production buyer deliverables. Without
                  this flag, the gate is in DEMO mode and SKIP is permitted
                  (e.g. H8 monocular fallback won't block but also won't ship
                  as production data).
```

---

## 5. Contact & Support

- **Email**: howard.linra@gmail.com
- **Vendor ID**: Register your vendor_id via email to receive bundle access.

---

## FAQ

### Q1: What does `exit 0` mean?
`exit 0` from `verify.sh` means all integrity checks passed — SHA-256 checksums match, the Ed25519 signature is valid, and the pubkey fingerprint is correct. The data is trusted.

### Q2: What if `verify.sh` returns a non-zero exit code?
Do **not** trust the data. A non-zero exit indicates either a hash mismatch (data was modified in transit), an invalid signature (bundle was not signed by the expected vendor), or a pubkey mismatch (signed by an unexpected key). Contact the vendor for a new bundle.

### Q3: Do I need to install any Python packages?
No. The verification script (`verify.sh`) and the gate smoke test use only Python standard library modules plus `cryptography` (which is bundled in the tarball). No `pip install` is required.

### Q4: What is `--strict-buyer` and when should I use it?
`--strict-buyer` is a flag for `bin/end_to_end_gate_smoke.py` that enforces production-grade validation. Without it, the tool runs in DEMO mode where SKIP results are permitted. With `--strict-buyer`, any SKIP or PASS_DEGRADED on H8/S1/V1/V2/B2 gates will cause an overall FAIL. Use it for all production buyer deliverables.

### Q5: Can I verify the bundle offline?
Yes. The entire verification process (`verify.sh` and `bin/end_to_end_gate_smoke.py`) works fully offline. No network access or external services are required. All cryptographic keys and checksums are embedded in the bundle.

---

*Auto-generated by `scripts/gen_quickstart.py` — do not edit manually.*
