#!/usr/bin/env python3
"""
i18n Linter for translation files

Verifies:
- All UI keys in en.json exist in zh-CN.json and ja-JP.json
- No empty strings
- Placeholder consistency: if en has {count} sessions, zh-CN must also have {count}
"""

import json
import re
import sys
from pathlib import Path
from typing import Dict, Set


def extract_placeholders(text: str) -> Set[str]:
    """Extract placeholder names from string like '{count} sessions'"""
    return set(re.findall(r"\{([^}]+)\}", text))


def load_json_file(filepath: Path) -> Dict:
    """Load JSON file with error handling"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error parsing {filepath}: {e}")
        sys.exit(1)
    except FileNotFoundError:
        print(f"File not found: {filepath}")
        sys.exit(1)


def lint_translations(i18n_dir: Path) -> bool:
    """Main linting function"""
    if not i18n_dir.exists():
        print(f"Directory not found: {i18n_dir}")
        return False

    # Load all language files
    en_file = i18n_dir / "en.json"
    zh_file = i18n_dir / "zh-CN.json"
    ja_file = i18n_dir / "ja-JP.json"

    en_data = load_json_file(en_file)
    zh_data = load_json_file(zh_file)
    ja_data = load_json_file(ja_file)

    all_errors = []
    warnings = []

    # Check 1: All keys in en.json exist in other languages
    en_keys = set(en_data.keys())
    zh_keys = set(zh_data.keys())
    ja_keys = set(ja_data.keys())

    missing_in_zh = en_keys - zh_keys
    missing_in_ja = en_keys - ja_keys

    if missing_in_zh:
        all_errors.append(f"Missing keys in zh-CN.json: {sorted(missing_in_zh)}")

    if missing_in_ja:
        all_errors.append(f"Missing keys in ja-JP.json: {sorted(missing_in_ja)}")

    # Check 2: No empty strings
    for key, value in en_data.items():
        if value == "":
            warnings.append(f"Empty string in en.json: '{key}'")

    for key, value in zh_data.items():
        if value == "":
            warnings.append(f"Empty string in zh-CN.json: '{key}'")

    for key, value in ja_data.items():
        if value == "":
            warnings.append(f"Empty string in ja-JP.json: '{key}'")

    # Check 3: Placeholder consistency
    for key in en_keys:
        if key in zh_keys:
            en_placeholders = extract_placeholders(str(en_data.get(key, "")))
            zh_placeholders = extract_placeholders(str(zh_data.get(key, "")))

            if en_placeholders != zh_placeholders:
                all_errors.append(
                    f"Placeholder mismatch for '{key}': "
                    f"en has {sorted(en_placeholders)}, "
                    f"zh-CN has {sorted(zh_placeholders)}"
                )

        if key in ja_keys:
            en_placeholders = extract_placeholders(str(en_data.get(key, "")))
            ja_placeholders = extract_placeholders(str(ja_data.get(key, "")))

            if en_placeholders != ja_placeholders:
                all_errors.append(
                    f"Placeholder mismatch for '{key}': "
                    f"en has {sorted(en_placeholders)}, "
                    f"ja-JP has {sorted(ja_placeholders)}"
                )

    # Check 4: Extra keys in translation files (not in English)
    extra_in_zh = zh_keys - en_keys
    extra_in_ja = ja_keys - en_keys

    if extra_in_zh:
        warnings.append(f"Extra keys in zh-CN.json (not in en.json): {sorted(extra_in_zh)}")

    if extra_in_ja:
        warnings.append(f"Extra keys in ja-JP.json (not in en.json): {sorted(extra_in_ja)}")

    # Report results
    if all_errors:
        print("❌ Lint errors found:")
        for error in all_errors:
            print(f"  - {error}")

    if warnings:
        print("⚠️  Lint warnings:")
        for warning in warnings:
            print(f"  - {warning}")

    if not all_errors and not warnings:
        print("✅ All checks passed!")

    # Summary
    print("\n📊 Summary:")
    print(f"  English keys: {len(en_keys)}")
    print(f"  Chinese keys: {len(zh_keys)}")
    print(f"  Japanese keys: {len(ja_keys)}")
    print(f"  Errors: {len(all_errors)}")
    print(f"  Warnings: {len(warnings)}")

    return len(all_errors) == 0


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="Lint i18n translation files")
    parser.add_argument(
        "i18n_dir",
        nargs="?",
        default="dashboard/i18n",
        help="Directory containing i18n JSON files (default: dashboard/i18n)",
    )

    args = parser.parse_args()

    i18n_dir = Path(args.i18n_dir)

    success = lint_translations(i18n_dir)

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
