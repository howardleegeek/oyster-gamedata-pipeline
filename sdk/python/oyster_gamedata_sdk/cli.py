"""Command-line entry point for the GameData SDK.

Usage:

    oyster-gamedata inspect <tarball-or-dir>
    oyster-gamedata validate <tarball-or-dir> [--json] [--output report.json]
    oyster-gamedata summary <tarball-or-dir>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .errors import GameDataSDKError
from .tarball import Tarball


def _print_summary(tar: Tarball) -> None:
    s = tar.metadata_summary()
    print(f"Clip root      : {s.clip_root}")
    if s.tarball_path:
        print(f"Source tarball : {s.tarball_path}")
    print(f"Game           : {s.systeminfo_game}")
    print(f"Resolution     : {s.systeminfo_resolution[0]}x{s.systeminfo_resolution[1]}")
    print(f"Video size     : {s.video_size_bytes:,} bytes")
    print(f"Action frames  : {s.n_action_frames}")
    if s.fps_first is not None:
        print(f"FPS (frame 0)  : {s.fps_first}")
    print(f"Route types    : {dict(s.route_type_distribution)}")
    print(f"Depth frames   : {s.n_depth_frames}")


def _cmd_inspect(args: argparse.Namespace) -> int:
    with Tarball.from_path(args.path) as tar:
        print(f"-- Tarball inspect: {args.path} --")
        _print_summary(tar)
        si = tar.systeminfo
        print(f"\nsysteminfo.json:")
        print(f"  gameProcessName: {si.game_process_name}")
        print(f"  window @ ({si.x},{si.y}) {si.width}x{si.height} dpi={si.record_dpi}")
        print(f"  map_scale={si.map_scale}  bounds={si.map_bounds}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    with Tarball.from_path(args.path) as tar:
        report = tar.validate()
        if args.json:
            payload = report.to_json(indent=2)
            if args.output:
                Path(args.output).write_text(payload)
                print(f"Report written to: {args.output}")
            else:
                print(payload)
        else:
            print(report.summary())
            if not report.passed:
                print("\nFailures:")
                for r in report.failed():
                    print(f"  [{r.criterion_id:2}] {r.name}: {r.message}")
            if args.output:
                Path(args.output).write_text(report.to_json(indent=2))
                print(f"\nFull JSON report: {args.output}")
        return 0 if report.passed else 1


def _cmd_summary(args: argparse.Namespace) -> int:
    with Tarball.from_path(args.path) as tar:
        summary = tar.metadata_summary()
        if args.json:
            print(json.dumps(summary.to_dict(), indent=2))
        else:
            _print_summary(tar)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="oyster-gamedata",
        description="Oyster GameData SDK — inspect, validate, and summarise buyer-spec v1 tarballs.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    insp = sub.add_parser("inspect", help="Print full tarball structure")
    insp.add_argument("path", type=str, help="Path to .tar.gz or extracted dir")
    insp.set_defaults(func=_cmd_inspect)

    val = sub.add_parser("validate", help="Run the 24-criterion lint")
    val.add_argument("path", type=str, help="Path to .tar.gz or extracted dir")
    val.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    val.add_argument("--output", "-o", type=str, default=None, help="Write JSON report to file")
    val.set_defaults(func=_cmd_validate)

    summ = sub.add_parser("summary", help="Quick metadata summary")
    summ.add_argument("path", type=str, help="Path to .tar.gz or extracted dir")
    summ.add_argument("--json", action="store_true", help="Emit JSON")
    summ.set_defaults(func=_cmd_summary)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except GameDataSDKError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
