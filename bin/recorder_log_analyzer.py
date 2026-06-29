#!/usr/bin/env python3
"""recorder_log_analyzer — classify OysterRecorder logs into known issues.

Howard 2026-05-08: feed this a diagnostic zip OR a raw OysterRecorder.log
file. It emits a JSON report with the recorder/MC version, the OS, and
every detected issue with its real log-line evidence.

Iron-law (data accuracy):
  - NEVER invent an issue. Every entry in `issues` must include a literal
    `evidence_line` lifted verbatim from the log. If the pattern doesn't
    match anywhere, the issue isn't reported.
  - NEVER guess severity. Severity is hard-coded per pattern.
  - NEVER classify an issue we haven't documented. Unknown errors go
    into a separate `unclassified` bucket so they get human attention
    rather than a wrong category.

Usage:
  python3 bin/recorder_log_analyzer.py path/to/OysterRecorder.log
  python3 bin/recorder_log_analyzer.py path/to/OysterRecorder_diagnostic.zip
  python3 bin/recorder_log_analyzer.py - < log_text   # stdin
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path


# =============================================================================
# Pattern catalogue
# =============================================================================
# Each pattern has:
#   code         — stable identifier used in spec dispatch
#   severity     — one of {critical, high, medium, low}
#   regex        — Python re; the matching line becomes evidence_line
#   summary      — human-readable description
#   suggested_spec — the spec id that addresses it (or "new" if no spec yet)
# =============================================================================

PATTERNS: list[dict] = [
    {
        "code": "FULL_DESKTOP_CAPTURE",
        "severity": "critical",
        "regex": re.compile(
            r"ffmpeg:\s*full-desktop capture\b.*title unsafe", re.IGNORECASE
        ),
        "summary": (
            "Recording captured the entire desktop instead of just the "
            "Minecraft window because gdigrab couldn't grab a non-ASCII "
            "window title. Privacy contract violated."
        ),
        "suggested_spec": "R01-recorder-iron-law-polish",
    },
    {
        "code": "PLACEHOLDER_GAMESTATE",
        "severity": "critical",
        "regex": re.compile(
            r"package:\s*no game-state JSONL.*placeholder", re.IGNORECASE
        ),
        "summary": (
            "Tarball shipped with placeholder camera/player coords "
            "(constant [0,64,0]) because the Fabric mod was not loaded. "
            "Iron-law violation: sells 'real' but ships fake."
        ),
        "suggested_spec": "R01-recorder-iron-law-polish",
    },
    {
        "code": "AUDIO_DEVICE_MISSING",
        "severity": "low",
        "regex": re.compile(
            r"audio_probe:\s*device=None|ffmpeg:\s*no audio device found",
            re.IGNORECASE,
        ),
        "summary": (
            "No audio capture device was available; recording is video-only. "
            "Acceptable for Minecraft (game audio is reproducible from the "
            "client) but worth knowing if the buyer spec demands audio."
        ),
        "suggested_spec": None,
    },
    {
        "code": "UPDATE_REFUSED_SINGLE_EXE",
        "severity": "low",
        "regex": re.compile(
            r"update:\s*SKIP — running as --onedir, refusing single-\.exe overwrite",
            re.IGNORECASE,
        ),
        "summary": (
            "Auto-updater refused to overwrite a --onedir bundle with a "
            "single-.exe build. Expected behaviour for users on the .zip "
            "format; they need to download the new .zip manually."
        ),
        "suggested_spec": None,
    },
    {
        "code": "FFMPEG_FATAL",
        "severity": "high",
        "regex": re.compile(
            r"ffmpeg:.*(error|fatal|cannot|failed)", re.IGNORECASE
        ),
        "summary": (
            "ffmpeg reported an error during capture or encoding. Most "
            "captures stop here; the tarball may be incomplete."
        ),
        "suggested_spec": None,
    },
    {
        "code": "UPLOAD_FAILED",
        "severity": "high",
        "regex": re.compile(
            r"upload:.*(failed|error|503|429|connection refused|timeout)",
            re.IGNORECASE,
        ),
        "summary": "Tarball upload to /api/upload-tarball did not complete.",
        "suggested_spec": None,
    },
    {
        "code": "UNCAUGHT_EXCEPTION",
        "severity": "high",
        "regex": re.compile(r"Traceback\s*\(most recent call last\)", re.IGNORECASE),
        "summary": "Python uncaught exception escaped to the log.",
        "suggested_spec": None,
    },
    {
        "code": "DEPTH_INFERENCE_INTERRUPTED",
        "severity": "medium",
        "regex": re.compile(
            r"depth:\s*running DepthAnything.*\n.*recording disarmed",
            re.IGNORECASE | re.DOTALL,
        ),
        "summary": (
            "User disarmed recording while DepthAnything V2 was still "
            "running. Tarball may be missing depth maps."
        ),
        "suggested_spec": None,
    },
]


@dataclass
class Issue:
    code: str
    severity: str
    summary: str
    evidence_line: str
    line_no: int
    suggested_spec: str | None = None


@dataclass
class RunInfo:
    recorder_version: str | None = None
    mc_version: str | None = None
    platform: str | None = None
    python: str | None = None
    log_size_bytes: int | None = None


@dataclass
class Report:
    source: str
    run: RunInfo
    issues: list[Issue] = field(default_factory=list)
    unclassified_errors: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        """Serialize the report to a JSON string.

        Returns:
            JSON string representation of the report with run info,
            issues, and summary counts.
        """
        return json.dumps(
            {
                "source": self.source,
                "run": asdict(self.run),
                "issues": [asdict(i) for i in self.issues],
                "unclassified_errors": self.unclassified_errors,
                "issue_count": len(self.issues),
                "critical_count": sum(1 for i in self.issues if i.severity == "critical"),
            },
            indent=2,
            ensure_ascii=False,
        )


# =============================================================================
# Loaders
# =============================================================================

def load_log_and_sysinfo(path: Path | None) -> tuple[str, dict[str, str]]:
    """Return (log_text, sysinfo_dict). Handles .log, .zip, or stdin."""
    if path is None:
        return sys.stdin.read(), {}

    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            log_text = ""
            sysinfo_text = ""
            for name in zf.namelist():
                if name.lower().endswith("oysterrecorder.log"):
                    log_text = zf.read(name).decode("utf-8", errors="replace")
                elif name.lower().endswith("sysinfo.txt"):
                    sysinfo_text = zf.read(name).decode("utf-8", errors="replace")
            return log_text, _parse_sysinfo(sysinfo_text)

    return path.read_text(encoding="utf-8", errors="replace"), {}


def _parse_sysinfo(text: str) -> dict[str, str]:
    """Parse `key: value` lines into a dict. Stable across CLI versions."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out


# =============================================================================
# Analysis
# =============================================================================

def extract_run_info(log_text: str, sysinfo: dict[str, str]) -> RunInfo:
    """Pull version + platform + size from sysinfo first, log second."""
    info = RunInfo()

    # Sysinfo takes priority (more reliable when present).
    info.recorder_version = sysinfo.get("recorder_version") or _grep1(
        log_text, r"current=([\w\.\-]+)", group=1
    )
    info.platform = sysinfo.get("platform")
    info.python = sysinfo.get("python")
    if size := sysinfo.get("log_size_bytes"):
        try:
            info.log_size_bytes = int(size)
        except ValueError:
            pass

    # MC version is in the recorder log, embedded in mc_window dict.
    info.mc_version = _grep1(log_text, r"Minecraft\s+([\d\.]+(?:\s*Snapshot\s*\d+)?)")

    return info


def _grep1(text: str, pattern: str, group: int = 1) -> str | None:
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(group).strip() if m else None


def classify(log_text: str) -> tuple[list[Issue], list[str]]:
    """Run all patterns; return (matched issues, unclassified error lines)."""
    issues: list[Issue] = []
    matched_lines: set[int] = set()

    lines = log_text.splitlines()
    for pat in PATTERNS:
        # For multi-line patterns (DOTALL), search whole text and find the
        # starting line by scanning. For single-line, iterate lines.
        if pat["regex"].flags & re.DOTALL:
            m = pat["regex"].search(log_text)
            if m:
                start_line = log_text[: m.start()].count("\n") + 1
                evidence = lines[start_line - 1] if 0 < start_line <= len(lines) else m.group(0).splitlines()[0]
                issues.append(
                    Issue(
                        code=pat["code"],
                        severity=pat["severity"],
                        summary=pat["summary"],
                        evidence_line=evidence,
                        line_no=start_line,
                        suggested_spec=pat["suggested_spec"],
                    )
                )
                matched_lines.add(start_line)
        else:
            for i, line in enumerate(lines, start=1):
                if pat["regex"].search(line):
                    issues.append(
                        Issue(
                            code=pat["code"],
                            severity=pat["severity"],
                            summary=pat["summary"],
                            evidence_line=line.rstrip(),
                            line_no=i,
                            suggested_spec=pat["suggested_spec"],
                        )
                    )
                    matched_lines.add(i)
                    break  # one report per pattern is enough; pattern is documented

    # Unclassified: look for ERROR/FAIL/Exception lines we didn't already catch.
    unclassified: list[str] = []
    err_re = re.compile(r"\b(ERROR|FAIL|Exception|panic|FATAL)\b", re.IGNORECASE)
    for i, line in enumerate(lines, start=1):
        if i in matched_lines:
            continue
        if err_re.search(line):
            unclassified.append(f"L{i}: {line.rstrip()}")

    return issues, unclassified


# =============================================================================
# CLI
# =============================================================================

def main(argv: list[str] | None = None) -> int:
    """Classify an OysterRecorder log file against known failure patterns.

    Args:
        argv: Command-line arguments. Defaults to sys.argv if None.
            Expected: [<script>] <source>
            source: Path to .log file, diagnostic .zip, or '-' for stdin.

    Returns:
        int: Exit code. 0 = no critical issues, 1 = critical issues found,
             2 = file error (not found or empty).

    Raises:
        SystemExit: Propagated from argparse on --help.
    """
    p = argparse.ArgumentParser(
        description="Classify OysterRecorder log against known failure patterns."
    )
    p.add_argument(
        "source",
        help="Path to .log file, diagnostic .zip, or '-' for stdin.",
    )
    args = p.parse_args(argv)

    src = args.source
    if src == "-":
        log_text, sysinfo = load_log_and_sysinfo(None)
        source_label = "<stdin>"
    else:
        path = Path(src)
        if not path.exists():
            print(f"error: source not found: {src}", file=sys.stderr)
            return 2
        log_text, sysinfo = load_log_and_sysinfo(path)
        source_label = str(path)

    if not log_text.strip():
        print("error: log content is empty", file=sys.stderr)
        return 2

    run = extract_run_info(log_text, sysinfo)
    issues, unclassified = classify(log_text)

    report = Report(
        source=source_label,
        run=run,
        issues=issues,
        unclassified_errors=unclassified,
    )
    print(report.to_json())

    # Exit code: 1 if any critical issue, else 0.
    return 1 if any(i.severity == "critical" for i in issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())
