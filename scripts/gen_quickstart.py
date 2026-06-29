#!/usr/bin/env python3
"""Generate docs/Quickstart.md from live tool --help output and CHANGELOG.md.

Usage:
    python3 scripts/gen_quickstart.py

This script:
1. Runs ``bin/provenance_verify.py --help`` and ``bin/end_to_end_gate_smoke.py --help``
   via subprocess to extract usage, options, and descriptions.
2. Reads CHANGELOG.md for the latest version and notable changes.
3. Fills in a template to produce docs/Quickstart.md.

Run once per release to keep Quickstart.md in sync with actual CLI behaviour.
"""

import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = ROOT / "bin"
DOCS_DIR = ROOT / "docs"
CHANGELOG = ROOT / "CHANGELOG.md"
OUTPUT = DOCS_DIR / "Quickstart.md"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_help(script: str) -> str:
    """Run ``python3 bin/<script> --help`` and return stdout."""
    result = subprocess.run(
        [sys.executable, str(BIN_DIR / script), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout


def _extract_options(help_text: str) -> list[dict]:
    """Parse --help output into a list of {flag, description} dicts."""
    options: list[dict] = []
    # Match lines like "  --flag  description" or "  -f, --flag  description"
    pattern = re.compile(r"^\s+(-[\w-]+(?:,\s*--[\w-]+)?)\s+(.+)$", re.MULTILINE)
    for match in pattern.finditer(help_text):
        flag = match.group(1).strip()
        desc = match.group(2).strip()
        options.append({"flag": flag, "description": desc})
    return options


def _extract_positional(help_text: str) -> list[dict]:
    """Extract positional arguments from --help output."""
    positionals: list[dict] = []
    # Match lines like "  positional_arg  description"
    # Look for the "positional arguments:" section
    pos_section = re.search(r"positional arguments:\s*\n((?:\s+.+\n)*)", help_text)
    if pos_section:
        for raw_line in pos_section.group(1).strip().split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                positionals.append({"name": parts[0], "description": parts[1]})
            elif len(parts) == 1:
                positionals.append({"name": parts[0], "description": ""})
    return positionals


def _get_latest_version() -> str:
    """Extract the latest version string from CHANGELOG.md."""
    if not CHANGELOG.exists():
        return "v0.0.0"
    text = CHANGELOG.read_text(encoding="utf-8")
    match = re.search(r"##\s+(v?\S+)\s+·\s+(\d{4}-\d{2}-\d{2})", text)
    if match:
        return f"{match.group(1)} · {match.group(2)}"
    match = re.search(r"##\s+(v?\S+)", text)
    if match:
        return match.group(1)
    return "v0.0.0"


def _get_changelog_highlights() -> str:
    """Extract the 'Added' section from the latest CHANGELOG entry."""
    if not CHANGELOG.exists():
        return ""
    text = CHANGELOG.read_text(encoding="utf-8")
    # Find the first "### Added" section
    added_match = re.search(r"### Added\s*\n((?:- .+\n)*)", text)
    if added_match:
        return added_match.group(1).strip()
    return ""


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------

TEMPLATE = """\
# Quickstart — Buyer Data Pipeline

> **Audience**: Buyers receiving a `.tar.gz` data bundle.
> **Time**: ≤ 5 minutes.
> **Prerequisites**: Python 3.10+ only. No other dependencies required.
> **Release**: {{VERSION}}

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

{{PROVENANCE_HELP}}

---

## 4. (Optional) Run End-to-End Gate Smoke Test

For deeper validation, run the full gate suite against a session directory:

```bash
python3 bin/end_to_end_gate_smoke.py <session_dir> --strict-buyer
```

### Flags

{{E2E_OPTIONS_TABLE}}

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

{{E2E_HELP}}

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
"""


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def _render_options_table(options: list[dict]) -> str:
    """Render options as a markdown table."""
    if not options:
        return "_No options available._"
    lines = ["| Flag | Description |", "|---|---|"]
    for opt in options:
        flag = opt["flag"].replace("|", "\\|")
        desc = opt["description"].replace("|", "\\|")
        lines.append(f"| `{flag}` | {desc} |")
    return "\n".join(lines)


def generate() -> str:
    """Generate the Quickstart.md content."""
    # Gather live help text
    prov_help = _run_help("provenance_verify.py")
    e2e_help = _run_help("end_to_end_gate_smoke.py")

    # Parse options
    e2e_options = _extract_options(e2e_help)
    options_table = _render_options_table(e2e_options)

    # Version info
    version = _get_latest_version()

    # Format help blocks as code
    prov_help_block = f"```\n{prov_help.strip()}\n```"
    e2e_help_block = f"```\n{e2e_help.strip()}\n```"

    # Fill template
    content = TEMPLATE
    content = content.replace("{{VERSION}}", version)
    content = content.replace("{{PROVENANCE_HELP}}", prov_help_block)
    content = content.replace("{{E2E_OPTIONS_TABLE}}", options_table)
    content = content.replace("{{E2E_HELP}}", e2e_help_block)

    return content


def main() -> None:
    """Entry point: generate and write Quickstart.md."""
    content = generate()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(content, encoding="utf-8")
    print(f"Generated {OUTPUT} ({len(content)} chars, {content.count(chr(10))} lines)")


if __name__ == "__main__":
    main()
