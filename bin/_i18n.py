#!/usr/bin/env python3
"""rc16.12 · bin/_i18n.py — minimal English/Chinese (zh-CN) localization helper.

Background: tester bingd is a Chinese-speaking Windows user. The Rust UI is
already partially Chinese ("▶ 开始录制" / "⏹ 停止录制"); the Python launcher's
Tk dialogs and Windows MessageBox surfaces were still English. This module
provides one tiny helper, ``_t(en, zh)``, that returns the localized string.

Detection (cheapest wins first):
    1. ``OYSTER_LANG`` env var — explicit override.
    2. ``GetUserDefaultUILanguage()`` on Windows — LCID 0x0804 = zh-CN.
    3. ``LANG`` env var starting with ``zh`` — POSIX fallback / unit-test path.
    4. Default to English. Detection must never raise (defensive: dialog
       text is the LAST thing we want to crash on).

Scope: USER-FACING strings only. Log messages stay English (operator-facing,
diagnostics for us). Rust UI strings are a separate concern (R-recorder).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("_i18n")

_LCID_ZH_CN: int = 0x0804  # Simplified Chinese (PRC); LANG_CHINESE + SUBLANG_CHINESE_SIMPLIFIED


def _detect_lang() -> str:
    """Return ``"zh-CN"`` or ``"en"``. Best-effort; never raises."""
    try:
        # Explicit override wins.
        override = os.environ.get("OYSTER_LANG", "").strip()
        if override.lower() in {"zh", "zh-cn", "zh_cn", "zhs"}:
            return "zh-CN"
        if override.lower() in {"en", "en-us", "en_us"}:
            return "en"
        # Windows: ask the OS for the user's UI language LCID.
        if os.name == "nt":
            try:
                import ctypes  # noqa: PLC0415 — lazy: stdlib-on-Windows
                kernel32 = ctypes.windll.kernel32
                lcid = int(kernel32.GetUserDefaultUILanguage())
                # Mask off the sortid; primary+sub language fits in low 16 bits.
                if (lcid & 0xFFFF) == _LCID_ZH_CN:
                    return "zh-CN"
            except Exception:  # noqa: BLE001 — detection must never crash
                logger.debug("rc16.12 _i18n: GetUserDefaultUILanguage failed",
                             exc_info=True)
        # POSIX (and Windows fallback): LANG env var, e.g. ``zh_CN.UTF-8``.
        lang_env = os.environ.get("LANG", "").strip().lower()
        if lang_env.startswith("zh"):
            return "zh-CN"
    except Exception:  # noqa: BLE001 — paranoia; module-import must not fail
        logger.debug("rc16.12 _i18n: locale detection raised", exc_info=True)
    return "en"


_LANG: str = _detect_lang()


def _t(en: str, zh: str) -> str:
    """rc16.12 i18n helper: return Chinese if locale=zh-CN, else English.

    Pure function. No I/O, no side effects, safe to call thousands of times.
    """
    return zh if _LANG == "zh-CN" else en


def current_lang() -> str:
    """Return the active language code (``"zh-CN"`` or ``"en"``). Test helper."""
    return _LANG
