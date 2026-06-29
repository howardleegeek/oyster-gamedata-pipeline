#!/usr/bin/env python3
"""
Test i18n coverage and consistency

Tests:
- Lint passes on all 3 languages
- Glossary terms appear consistently across docs
- No untranslated English in non-en strings (heuristic: warn if mostly-ASCII)
"""

import json
import re
import unittest
from pathlib import Path
from typing import Dict, Set


class TestI18nCoverage(unittest.TestCase):
    """Test suite for i18n coverage"""

    @classmethod
    def setUpClass(cls):
        cls.i18n_dir = Path("dashboard/i18n")
        cls.docs_dir = Path("docs")

        # Load i18n files
        cls.en_data = cls._load_json(cls.i18n_dir / "en.json")
        cls.zh_data = cls._load_json(cls.i18n_dir / "zh-CN.json")
        cls.ja_data = cls._load_json(cls.i18n_dir / "ja-JP.json")

        # Load glossary
        cls.glossary = cls._load_glossary(cls.docs_dir / "glossary.md")

        # Load documentation
        cls.onboarding_en = cls._load_text(cls.docs_dir / "ONBOARDING.md").lower()
        cls.onboarding_zh = cls._load_text(cls.docs_dir / "ONBOARDING.zh-CN.md")
        cls.onboarding_ja = cls._load_text(cls.docs_dir / "ONBOARDING.ja-JP.md")

    @staticmethod
    def _load_json(filepath: Path) -> Dict:
        """Load JSON file"""
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _load_text(filepath: Path) -> str:
        """Load text file"""
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def _load_glossary(filepath: Path) -> Dict[str, Dict[str, str]]:
        """Load glossary from markdown table"""
        glossary = {}
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Skip header lines
        for raw_line in lines[2:]:  # Skip title and separator
            line = raw_line.strip()
            if line.startswith("|") and line.endswith("|"):
                parts = [p.strip() for p in line[1:-1].split("|")]
                if len(parts) >= 3:
                    english = parts[0]
                    chinese = parts[1]
                    japanese = parts[2]
                    glossary[english.lower()] = {"en": english, "zh-CN": chinese, "ja-JP": japanese}

        return glossary

    def test_01_i18n_keys_exist(self):
        """Test that all English keys exist in other languages"""
        en_keys = set(self.en_data.keys())
        zh_keys = set(self.zh_data.keys())
        ja_keys = set(self.ja_data.keys())

        missing_in_zh = en_keys - zh_keys
        missing_in_ja = en_keys - ja_keys

        self.assertEqual(
            len(missing_in_zh), 0, f"Missing keys in zh-CN.json: {sorted(missing_in_zh)}"
        )
        self.assertEqual(
            len(missing_in_ja), 0, f"Missing keys in ja-JP.json: {sorted(missing_in_ja)}"
        )

    def test_02_no_empty_strings(self):
        """Test that there are no empty strings in translations"""
        empty_keys = []

        for key, value in self.en_data.items():
            if value == "":
                empty_keys.append(f"en.json: '{key}'")

        for key, value in self.zh_data.items():
            if value == "":
                empty_keys.append(f"zh-CN.json: '{key}'")

        for key, value in self.ja_data.items():
            if value == "":
                empty_keys.append(f"ja-JP.json: '{key}'")

        self.assertEqual(len(empty_keys), 0, f"Empty strings found: {empty_keys}")

    def test_03_placeholder_consistency(self):
        """Test that placeholders are consistent across languages"""

        def extract_placeholders(text: str) -> Set[str]:
            return set(re.findall(r"\{([^}]+)\}", text))

        errors = []

        for key in self.en_data.keys():
            if key in self.zh_data:
                en_ph = extract_placeholders(str(self.en_data[key]))
                zh_ph = extract_placeholders(str(self.zh_data[key]))

                if en_ph != zh_ph:
                    errors.append(f"{key}: en={en_ph}, zh-CN={zh_ph}")

            if key in self.ja_data:
                en_ph = extract_placeholders(str(self.en_data[key]))
                ja_ph = extract_placeholders(str(self.ja_data[key]))

                if en_ph != ja_ph:
                    errors.append(f"{key}: en={en_ph}, ja-JP={ja_ph}")

        self.assertEqual(len(errors), 0, f"Placeholder mismatches: {errors}")

    def test_04_key_glossary_terms_in_docs(self):
        """Test that key glossary terms appear in documentation"""
        # Only check key project-specific terms
        key_terms = {
            "session": ["session", "录制会话", "セッション"],
            "canonical pipeline": ["canonical pipeline", "标准管线", "標準パイプライン"],
            "audit": ["audit", "审计", "監査"],
            "provenance": ["provenance", "溯源", "プロベナンス"],
            "watchdog": ["watchdog", "监控守护", "ウォッチドッグ"],
            "route_type": ["route_type", "路线类型", "ルートタイプ"],
            "recording": ["recording", "录制", "記録"],
            "playback": ["playback", "回放", "再生"],
        }

        errors = []

        # Check English terms in English docs (case-insensitive)
        for term_name, translations in key_terms.items():
            english_term = translations[0].lower()
            if english_term and english_term not in ["", " ", "|", "-"]:
                if english_term not in self.onboarding_en:
                    # Try alternative forms
                    if term_name == "route_type":
                        if (
                            "route type" not in self.onboarding_en
                            and "route-type" not in self.onboarding_en
                        ):
                            errors.append(f"Key term '{english_term}' not found in English docs")
                    else:
                        errors.append(f"Key term '{english_term}' not found in English docs")

        # Check Chinese terms in Chinese docs
        for term_name, translations in key_terms.items():
            chinese_term = translations[1]
            if chinese_term and chinese_term not in ["", " ", "|", "-", "中文"]:
                if chinese_term not in self.onboarding_zh:
                    errors.append(
                        f"Key term '{chinese_term}' (en: '{translations[0]}') "
                        f"not found in Chinese docs"
                    )

        # Check Japanese terms in Japanese docs
        for term_name, translations in key_terms.items():
            japanese_term = translations[2]
            if japanese_term and japanese_term not in ["", " ", "|", "-", "日本語"]:
                if japanese_term not in self.onboarding_ja:
                    errors.append(
                        f"Key term '{japanese_term}' (en: '{translations[0]}') "
                        f"not found in Japanese docs"
                    )

        self.assertEqual(len(errors), 0, "\n".join(errors))

    def test_05_no_untranslated_english(self):
        """Heuristic test for untranslated English in non-English strings"""

        def is_mostly_ascii(text: str, threshold: float = 0.7) -> bool:
            """Check if text is mostly ASCII characters"""
            if not text:
                return False

            ascii_count = sum(1 for c in text if ord(c) < 128)
            return ascii_count / len(text) > threshold

        warnings = []

        # Check Chinese translations
        for key, value in self.zh_data.items():
            if is_mostly_ascii(value) and len(value) > 3:
                # Skip known English terms that should remain in English
                english_value = self.en_data.get(key, "")
                if value != english_value:  # Only warn if different from English
                    warnings.append(f"zh-CN '{key}': '{value}' looks like English")

        # Check Japanese translations
        for key, value in self.ja_data.items():
            if is_mostly_ascii(value) and len(value) > 3:
                # Skip known English terms that should remain in English
                english_value = self.en_data.get(key, "")
                if value != english_value:  # Only warn if different from English
                    warnings.append(f"ja-JP '{key}': '{value}' looks like English")

        # This is a warning test, not a failure
        if warnings:
            print("\n⚠️  Warning: Possible untranslated English detected:")
            for warning in warnings[:10]:  # Show first 10 warnings
                print(f"  - {warning}")
            if len(warnings) > 10:
                print(f"  ... and {len(warnings) - 10} more")

        # We don't fail the test for warnings, just print them

    def test_06_glossary_completeness(self):
        """Test that glossary has reasonable number of terms"""
        self.assertGreaterEqual(
            len(self.glossary),
            50,
            f"Glossary should have at least 50 terms, has {len(self.glossary)}",
        )

        # Check that key terms are present
        key_terms = [
            "session",
            "canonical pipeline",
            "audit",
            "provenance",
            "watchdog",
            "route_type",
            "recording",
            "playback",
        ]

        missing_terms = []
        for term in key_terms:
            if term.lower() not in self.glossary:
                missing_terms.append(term)

        self.assertEqual(len(missing_terms), 0, f"Missing key terms in glossary: {missing_terms}")

    def test_07_documentation_files_exist(self):
        """Test that all required documentation files exist"""
        required_files = [
            self.docs_dir / "ONBOARDING.md",
            self.docs_dir / "ONBOARDING.zh-CN.md",
            self.docs_dir / "ONBOARDING.ja-JP.md",
            self.docs_dir / "glossary.md",
        ]

        for filepath in required_files:
            self.assertTrue(filepath.exists(), f"Required file does not exist: {filepath}")

    def test_08_i18n_files_exist(self):
        """Test that all required i18n files exist"""
        required_files = [
            self.i18n_dir / "en.json",
            self.i18n_dir / "zh-CN.json",
            self.i18n_dir / "ja-JP.json",
        ]

        for filepath in required_files:
            self.assertTrue(filepath.exists(), f"Required i18n file does not exist: {filepath}")


if __name__ == "__main__":
    unittest.main()
