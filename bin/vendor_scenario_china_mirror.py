#!/usr/bin/env python3
"""
vendor_scenario_china_mirror.py — Walkthrough for vendors operating in China (CN)
behind the Great Firewall. Demonstrates pip mirror configuration and AWS S3
region selection so dependency installation and artifact uploads work without VPN.

Usage:
    python3 bin/vendor_scenario_china_mirror.py --mirror aliyun --s3-region cn-north-1
    python3 bin/vendor_scenario_china_mirror.py --dry-run --validate-only
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

PIP_MIRRORS: dict[str, str] = {
    "aliyun": "https://mirrors.aliyun.com/pypi/simple/",
    "tsinghua": "https://pypi.tuna.tsinghua.edu.cn/simple/",
    "douban": "https://pypi.douban.com/simple/",
    "huawei": "https://repo.huaweicloud.com/repository/pypi/simple/",
}
S3_CN_REGIONS: dict[str, str] = {
    "cn-north-1": "Beijing (AWS China)",
    "cn-northwest-1": "Ningxia (AWS China)",
}
TRUSTED_HOSTS: dict[str, str] = {
    "aliyun": "mirrors.aliyun.com",
    "tsinghua": "pypi.tuna.tsinghua.edu.cn",
    "douban": "pypi.douban.com",
    "huawei": "repo.huaweicloud.com",
}


def _run(cmd: list[str], *, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run a subprocess safely (no shell=True)."""
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)


def _write_pip_conf(dest: Path, mirror_name: str) -> None:
    """Generate a pip.conf pointing to the selected CN mirror."""
    dest.write_text(
        f"[global]\nindex-url = {PIP_MIRRORS[mirror_name]}\n"
        f"trusted-host = {TRUSTED_HOSTS[mirror_name]}\ntimeout = 30\n",
        encoding="utf-8",
    )


def _write_aws_config(dest: Path, region: str) -> None:
    """Generate an AWS config template for the selected CN region."""
    dest.write_text(
        f"[default]\nregion = {region}\ns3 =\n    addressing_style = virtual\n",
        encoding="utf-8",
    )


def validate_s3_region(region: str) -> bool:
    """Confirm the region is a known AWS China region."""
    return region in S3_CN_REGIONS


def run_scenario(
    mirror_name: str,
    s3_region: str,
    *,
    dry_run: bool = False,
    validate_only: bool = False,
) -> int:
    """Execute the full vendor-in-CN walkthrough. Returns 0 on success."""
    print(f"[G060] Mirror : {mirror_name} -> {PIP_MIRRORS[mirror_name]}")
    print(f"[G060] S3     : {s3_region} -> {S3_CN_REGIONS.get(s3_region, 'unknown')}")

    if validate_only:
        ok = mirror_name in PIP_MIRRORS and validate_s3_region(s3_region)
        print(f"[G060] Mirror valid : {mirror_name in PIP_MIRRORS}")
        print(f"[G060] Region valid : {validate_s3_region(s3_region)}")
        return 0 if ok else 1

    with tempfile.TemporaryDirectory(prefix="g060_vendor_") as tmpdir:
        tmp = Path(tmpdir)

        # Step 1: Write pip.conf
        pip_conf = tmp / "pip.conf"
        _write_pip_conf(pip_conf, mirror_name)
        print(f"[G060] Wrote pip.conf  -> {pip_conf}")
        print(f"[G060]   {pip_conf.read_text().strip()}")

        # Step 2: Write AWS config
        aws_dir = tmp / ".aws"
        aws_dir.mkdir()
        _write_aws_config(aws_dir / "config", s3_region)
        print(f"[G060] Wrote aws config -> {aws_dir / 'config'}")

        # Step 3: Verify pip can resolve with the mirror
        env = os.environ.copy()
        env["PIP_CONFIG_FILE"] = str(pip_conf)
        env["AWS_CONFIG_FILE"] = str(aws_dir / "config")

        if not dry_run:
            result = _run(
                [sys.executable, "-m", "pip", "index", "versions", "requests"],
                env=env,
            )
            if result.returncode == 0:
                print(f"[G060] pip index OK: {result.stdout.strip()[:120]}")
            else:
                print(f"[G060] pip index fallback: {result.stderr.strip()[:120]}")

        # Step 4: Emit summary JSON
        summary = {
            "mirror": mirror_name,
            "mirror_url": PIP_MIRRORS[mirror_name],
            "s3_region": s3_region,
            "s3_label": S3_CN_REGIONS.get(s3_region, ""),
            "pip_conf": str(pip_conf),
            "aws_config": str(aws_dir / "config"),
            "status": "success",
        }
        summary_path = tmp / "summary.json"
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"[G060] Summary -> {summary_path.read_text()}")

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry-point with argparse CLI."""
    parser = argparse.ArgumentParser(
        description="G060 — Vendor-in-CN walkthrough: pip mirror + S3 region.",
    )
    parser.add_argument(
        "--mirror", choices=list(PIP_MIRRORS), default="aliyun",
        help="CN pip mirror to use (default: aliyun).",
    )
    parser.add_argument(
        "--s3-region", choices=list(S3_CN_REGIONS), default="cn-north-1",
        help="AWS S3 China region (default: cn-north-1).",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print configuration without network calls.")
    parser.add_argument("--validate-only", action="store_true",
                        help="Only validate mirror name and region, then exit.")
    args = parser.parse_args(argv)
    return run_scenario(
        args.mirror, args.s3_region,
        dry_run=args.dry_run, validate_only=args.validate_only,
    )


if __name__ == "__main__":
    sys.exit(main())
