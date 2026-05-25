#!/usr/bin/env python3
"""Machine-checkable gate for promoting OysterRecorder builds.

The release, installer, and backend smokes prove that an internal build can be
downloaded and exercised. Production promotion needs stricter evidence: HTTPS,
signed installer, a strict GUI real-session report, backend counter delta, and
non-stub providers.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STUB_PROVIDER_VALUES = {
    "",
    "dev",
    "demo",
    "fake",
    "local",
    "mock",
    "none",
    "sim",
    "simulator",
    "stub",
    "test",
}


@dataclass(frozen=True)
class GateConfig:
    mode: str
    backend_url: str
    expected_release_tag: str
    real_session_report: Path | None
    installer_authenticode_status: str
    oauth_provider: str
    storage_provider: str
    payout_provider: str
    minimum_game_state_rows: int = 30
    minimum_video_bytes: int = 102400


@dataclass(frozen=True)
class GateCheck:
    name: str
    status: str
    detail: str = ""
    required: bool = True


@dataclass
class GateReport:
    mode: str
    checks: list[GateCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.status == "pass" for check in self.checks if check.required)

    def add(self, name: str, status: str, detail: str = "", required: bool = True) -> None:
        if status not in {"pass", "warn", "fail"}:
            raise ValueError(f"invalid gate status: {status}")
        self.checks.append(GateCheck(name=name, status=status, detail=detail, required=required))

    def summary(self) -> str:
        lines = [f"Production readiness gate ({self.mode})"]
        for check in self.checks:
            suffix = "" if check.required else " optional"
            detail = f" - {check.detail}" if check.detail else ""
            lines.append(f"  [{check.status.upper()}] {check.name}{suffix}{detail}")
        required = [check for check in self.checks if check.required]
        passed = sum(1 for check in required if check.status == "pass")
        lines.append(f"\n  {passed}/{len(required)} required checks passed")
        return "\n".join(lines)


def _normalise_provider(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def _provider_is_real(value: str) -> bool:
    return _normalise_provider(value) not in STUB_PROVIDER_VALUES


def _read_current_consumer_tag(repo_root: Path) -> str:
    release_channels = repo_root / "src" / "oyster_agent_runner" / "release_channels.py"
    tree = ast.parse(release_channels.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            getattr(target, "id", None) == "CURRENT_CONSUMER_TAG" for target in node.targets
        ):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value
    raise RuntimeError("CURRENT_CONSUMER_TAG not found in release_channels.py")


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _int_at(payload: dict[str, Any], path: tuple[str, ...], default: int = 0) -> int:
    value: Any = payload
    for key in path:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float):
        return int(value)
    return default


def _dict_at(payload: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
    value: Any = payload
    for key in path:
        if not isinstance(value, dict):
            return {}
        value = value.get(key)
    return value if isinstance(value, dict) else {}


def _bool_at(payload: dict[str, Any], path: tuple[str, ...]) -> bool:
    value: Any = payload
    for key in path:
        if not isinstance(value, dict):
            return False
        value = value.get(key)
    return bool(value)


def evaluate_gate(config: GateConfig) -> GateReport:
    if config.mode not in {"internal", "production"}:
        raise ValueError("mode must be 'internal' or 'production'")

    report = GateReport(mode=config.mode)
    production = config.mode == "production"

    if config.expected_release_tag.startswith("v"):
        report.add("release-anchor", "pass", config.expected_release_tag)
    else:
        report.add("release-anchor", "fail", "expected release tag must start with v")

    if config.backend_url.startswith(("http://", "https://")):
        report.add("backend-url-configured", "pass", config.backend_url)
    else:
        report.add("backend-url-configured", "fail", "backend URL must be http(s)")

    if config.backend_url.startswith("https://"):
        report.add("backend-url-https", "pass", config.backend_url, required=production)
    else:
        status = "fail" if production else "warn"
        report.add("backend-url-https", status, "production requires HTTPS", required=production)

    _add_provider_check(report, "oauth-provider-real", config.oauth_provider, required=production)
    _add_provider_check(
        report, "storage-provider-real", config.storage_provider, required=production
    )
    _add_provider_check(report, "payout-provider-real", config.payout_provider, required=production)

    evidence_required = production
    if not config.real_session_report:
        _add_installer_signature_check(
            report, config.installer_authenticode_status, required=production
        )
        status = "fail" if evidence_required else "warn"
        report.add(
            "strict-real-session-report-present",
            status,
            "no real-session report provided",
            required=evidence_required,
        )
        return report
    if not config.real_session_report.exists():
        _add_installer_signature_check(
            report, config.installer_authenticode_status, required=production
        )
        status = "fail" if evidence_required else "warn"
        report.add(
            "strict-real-session-report-present",
            status,
            f"missing {config.real_session_report}",
            required=evidence_required,
        )
        return report

    payload = _load_json(config.real_session_report)
    report.add(
        "strict-real-session-report-present",
        "pass",
        str(config.real_session_report),
        required=evidence_required,
    )

    report_signature = str(_dict_at(payload, ("installer",)).get("authenticode_status", ""))
    _add_installer_signature_check(
        report,
        config.installer_authenticode_status or report_signature,
        required=production,
    )

    _add_report_bool(
        report,
        "strict-real-session-enabled",
        _bool_at(payload, ("real_session", "strict")),
        "report.real_session.strict must be true",
        required=evidence_required,
    )
    _add_report_bool(
        report,
        "no-gui-preflight-disabled",
        not _bool_at(payload, ("no_gui_preflight",)),
        "production evidence cannot be a no-GUI preflight",
        required=evidence_required,
    )
    _add_report_bool(
        report,
        "upload-delta-required",
        _bool_at(payload, ("admin_state", "require_upload_delta")),
        "report.admin_state.require_upload_delta must be true",
        required=evidence_required,
    )

    rows = _int_at(payload, ("real_session", "game_state", "rows"))
    if rows >= config.minimum_game_state_rows:
        report.add("fresh-game-state-rows", "pass", str(rows), required=evidence_required)
    else:
        status = "fail" if evidence_required else "warn"
        report.add(
            "fresh-game-state-rows",
            status,
            f"{rows} < {config.minimum_game_state_rows}",
            required=evidence_required,
        )

    video_bytes = _int_at(payload, ("real_session", "video", "size_bytes"))
    if video_bytes >= config.minimum_video_bytes:
        report.add("fresh-video-bytes", "pass", str(video_bytes), required=evidence_required)
    else:
        status = "fail" if evidence_required else "warn"
        report.add(
            "fresh-video-bytes",
            status,
            f"{video_bytes} < {config.minimum_video_bytes}",
            required=evidence_required,
        )

    manifest_bytes = _int_at(payload, ("real_session", "manifest", "size_bytes"))
    if manifest_bytes > 0:
        report.add(
            "fresh-manifest-present", "pass", f"{manifest_bytes} bytes", required=evidence_required
        )
    else:
        status = "fail" if evidence_required else "warn"
        report.add(
            "fresh-manifest-present", status, "missing manifest bytes", required=evidence_required
        )

    delta_uploads = _int_at(payload, ("admin_state", "delta", "uploads"))
    delta_sessions = _int_at(payload, ("admin_state", "delta", "sessions"))
    if delta_uploads > 0 or delta_sessions > 0:
        report.add(
            "backend-upload-or-session-delta",
            "pass",
            f"uploads={delta_uploads}; sessions={delta_sessions}",
            required=evidence_required,
        )
    else:
        status = "fail" if evidence_required else "warn"
        report.add(
            "backend-upload-or-session-delta",
            status,
            f"uploads={delta_uploads}; sessions={delta_sessions}",
            required=evidence_required,
        )

    return report


def _add_provider_check(report: GateReport, name: str, value: str, required: bool) -> None:
    if _provider_is_real(value):
        report.add(name, "pass", value, required=required)
        return
    status = "fail" if required else "warn"
    report.add(name, status, f"got {value or '<unset>'}", required=required)


def _add_installer_signature_check(report: GateReport, value: str, required: bool) -> None:
    status_value = value.strip()
    if status_value == "Valid":
        report.add("installer-authenticode-valid", "pass", status_value, required=required)
        return
    status = "fail" if required else "warn"
    report.add(
        "installer-authenticode-valid",
        status,
        f"got {status_value or '<missing>'}",
        required=required,
    )


def _add_report_bool(
    report: GateReport,
    name: str,
    passed: bool,
    detail: str,
    required: bool,
) -> None:
    if passed:
        report.add(name, "pass", required=required)
        return
    status = "fail" if required else "warn"
    report.add(name, status, detail, required=required)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["internal", "production"], default="internal")
    parser.add_argument(
        "--backend-url",
        default=os.getenv("BACKEND_SMOKE_URL", "https://136-109-41-170.sslip.io"),
    )
    parser.add_argument("--expected-release-tag", default="")
    parser.add_argument("--real-session-report", type=Path)
    parser.add_argument(
        "--installer-authenticode-status",
        default=os.getenv("OYSTER_INSTALLER_AUTHENTICODE_STATUS", ""),
    )
    parser.add_argument("--oauth-provider", default=os.getenv("GAMEDATA_OAUTH_PROVIDER", "mock"))
    parser.add_argument(
        "--storage-provider", default=os.getenv("GAMEDATA_STORAGE_PROVIDER", "local")
    )
    parser.add_argument(
        "--payout-provider",
        default=os.getenv("GAMEDATA_PAYOUT_PROVIDER", "simulator"),
    )
    parser.add_argument("--minimum-game-state-rows", type=int, default=30)
    parser.add_argument("--minimum-video-bytes", type=int, default=102400)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parent.parent
    expected_release_tag = args.expected_release_tag or _read_current_consumer_tag(repo_root)
    report = evaluate_gate(
        GateConfig(
            mode=args.mode,
            backend_url=args.backend_url,
            expected_release_tag=expected_release_tag,
            real_session_report=args.real_session_report,
            installer_authenticode_status=args.installer_authenticode_status,
            oauth_provider=args.oauth_provider,
            storage_provider=args.storage_provider,
            payout_provider=args.payout_provider,
            minimum_game_state_rows=args.minimum_game_state_rows,
            minimum_video_bytes=args.minimum_video_bytes,
        )
    )
    print(report.summary())
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
